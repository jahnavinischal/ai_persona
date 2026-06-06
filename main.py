import json
import os
import httpx
import asyncio
import time
import re
import threading

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from groq import AsyncGroq
from dotenv import load_dotenv

from persona import SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT
from cal_tool import get_available_slots, book_meeting, SLOT_MAP
from contextlib import asynccontextmanager

import sys
sys.path.append(".")
from rag.retriever import get_context

import os
from dotenv import load_dotenv

# Explicitly set HF token
hf_token = os.getenv("HF_TOKEN")

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
load_dotenv()

os.environ["CHROMA_CACHE_DIR"] = "/opt/render/.cache/chroma"


def run_ingestion_sync():
    try:
        import chromadb
        client = chromadb.PersistentClient(path="./chroma_db")
        collection = client.get_or_create_collection("persona")
        if collection.count() == 0:
            print("Starting ingestion...")
            from rag.ingest import ingest_resume, ingest_github, ingest_commits
            ingest_resume("resume.pdf")
            ingest_github("jahnavinischal")
            ingest_commits("jahnavinischal")
            print("Ingestion complete!")
        else:
            print(f"Skipping ingestion — {collection.count()} chunks already stored")
    except Exception as e:
        print(f"Ingestion error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start ingestion in a separate thread- doesn't block port binding
    thread = threading.Thread(target=run_ingestion_sync, daemon=True)
    thread.start()
    yield

app = FastAPI(lifespan=lifespan)

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

GROQ_KEYS = [
    os.getenv("GROQ_API_KEY"),
    os.getenv("GROQ_API_KEY_2"), 
    os.getenv("GROQ_API_KEY_3")

]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_availability",
            "description": "Check available interview slots for the candidate",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_meeting",
            "description": "Book a confirmed interview slot once you have name, email, and slot. The slot must be the exact ISO datetime string returned by get_availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "Caller's full name"},
                    "email": {"type": "string", "description": "Caller's email address"},
                    "slot":  {"type": "string",     "description": "The slot label exactly as returned by get_availability e.g. '2026-06-05 at 11:00 IST'"
},
                },
                "required": ["name", "email", "slot"],
            },
        },
    },
]

async def run_tool(name: str, args: dict) -> str:
    print("TOOL CALLED:", name, "ARGS:", args)

    if name == "get_availability":
        slots = await get_available_slots()
        if not slots:
            return "No slots available this week."
        return "Available slots: " + " | ".join(slots)

    if name == "book_meeting":
        # Resolve label to ISO if needed
        slot = args.get("slot", "")
        if slot in SLOT_MAP:
            args["slot"] = SLOT_MAP[slot]
        elif "T" not in slot:
            # LLM passed a human label we don't recognize — get fresh slots
            return "I couldn't find that slot. Let me check availability again."
        try:
            result = await book_meeting(**args)
            data = result.get("data", result)
            uid = data.get("uid")
            if uid:
                return f"Booking confirmed! Reference ID: {uid}. Confirmation sent to {args['email']}."
            return f"Booking failed — response was: {result}"
        except httpx.HTTPStatusError as e:
            print("CAL ERROR:", e.response.status_code, e.response.text)
            body = e.response.text
            if "already" in body.lower() or "not available" in body.lower():
                return "That slot is just taken. Call get_availability again for fresh slots."
            return f"Booking failed ({e.response.status_code}): {body}"
        except Exception as e:
            print("UNEXPECTED ERROR:", type(e), e)
            return f"Booking error: {str(e)}"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

async def call_groq(messages: list, max_tokens: int = 300) -> object:
    for key in GROQ_KEYS:
        if not key:
            continue
        for model in GROQ_MODELS:
            try:
                client = AsyncGroq(api_key=key)
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                        max_tokens=max_tokens,
                        temperature=0.4,
                    ),
                    timeout=8.0  # fail fast, don't wait forever
                )
                print(f"Used model: {model}")
                return response.choices[0].message
            except asyncio.TimeoutError:
                print(f"Timeout on {model}, trying next...")
                continue
            except Exception as e:
                if "rate_limit" in str(e).lower() or "429" in str(e) or "decommissioned" in str(e).lower():
                    print(f"Rate limit on {model}, trying next...")
                    continue
                raise
    raise Exception("All Groq keys and models exhausted")

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    message = await call_groq(full_messages)

    # Handle tool call
    if message.tool_calls:
        tool_call = message.tool_calls[0]
        fn_name = tool_call.function.name
        fn_args = json.loads(tool_call.function.arguments or "{}")

        tool_result = await run_tool(fn_name, fn_args)

        follow_up_messages = [
            *full_messages,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        }
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            },
        ]

        final_msg = await call_groq(follow_up_messages, max_tokens=150)
        final_content = final_msg.content or "Your interview has been booked successfully!"

        return JSONResponse({"choices": [{"message": {"role": "assistant", "content": final_content}}]})

    # Normal response (no tool call)
    return JSONResponse({"choices": [{"message": {"role": "assistant", "content": message.content}}]})

@app.post("/vapi-webhook")
async def vapi_webhook(request: Request):
    """
    Vapi calls this at call start to get assistant configuration.
    """
    body = await request.json()
    msg_type = body.get("message", {}).get("type")

    if msg_type == "assistant-request":
        return JSONResponse({
            "assistant": {
                "firstMessage": (
                    "Hi! I'm the AI representative for [Your Name]. "
                    "I can tell you about their background, answer questions about "
                    "their work, and help you schedule an interview. What would you like to know?"
                ),
                "model": {
                    "provider": "custom-llm",
                    "url": f"{os.getenv('SERVER_URL')}/chat",
                },
                "voice": {
                    "provider": "elevenlabs",
                    "voiceId": "rachel",
                },
                "endCallFunctionEnabled": True,
                "recordingEnabled": True,
            }
        })

    # Vapi sends other event types (call ended, transcript, etc.), acknowledge them
    return JSONResponse({"status": "ok"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/debug-env")
async def debug_env():
    return {
        "cal_key_set": bool(os.getenv("CAL_API_KEY")),
        "event_type_id": os.getenv("CAL_EVENT_TYPE_ID"),
    }


@app.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    # Strip any system message Vapi sends
    messages = [m for m in messages if m.get("role") != "system"]

    # Strip interrupted assistant messages from history
    if (len(messages) >= 2 and
        messages[-1].get("role") == "user" and
        messages[-2].get("role") == "assistant"):
        last_assistant = messages[-2].get("content", "") or ""
        if len(last_assistant.strip()) < 20:
            messages = [m for m in messages if m != messages[-2]]
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    start = time.time()
    message = await call_groq(full_messages)
    latency = time.time() - start
    print(f"LATENCY: {latency:.3f}s")

    if message.tool_calls:
        tool_call = message.tool_calls[0]
        fn_name = tool_call.function.name
        fn_args = json.loads(tool_call.function.arguments or "{}")
        tool_result = await run_tool(fn_name, fn_args)

        follow_up_messages = [
            *full_messages,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    }
                }]
            },
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            },
        ]

        final_msg = await call_groq(follow_up_messages, max_tokens=150)
        content = final_msg.content or "Your interview has been booked successfully!"

    else:
        content = message.content

    if stream:
        async def generate():
            chunk = {
                "choices": [{
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                    "index": 0
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            done = {
                "choices": [{
                    "delta": {},
                    "finish_reason": "stop",
                    "index": 0
                }]
            }
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    return JSONResponse({"choices": [{"message": {"role": "assistant", "content": content}}]})

@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    user_message = messages[-1]["content"] if messages else ""
    # RAG — retrieve relevant context
    context = await asyncio.get_event_loop().run_in_executor(
        None, get_context, user_message
    )

    rag_system_prompt = f"""
{CHAT_SYSTEM_PROMPT}

RELEVANT CONTEXT FROM RESUME AND GITHUB:
{context}

STRICT RULES:
- Only state specific technical details if they appear in the context above
- If not in context: "I don't have those specifics, but Jahnavi can walk you through it in the interview"
- Never hallucinate technical details not present in the context
"""
    
    full_messages = [{"role": "system", "content": rag_system_prompt}] + messages
    message = await call_groq(full_messages)
    if message.tool_calls:
        tool_call = message.tool_calls[0]
        fn_name = tool_call.function.name
        fn_args = json.loads(tool_call.function.arguments or "{}")
        tool_result = await run_tool(fn_name, fn_args)
        follow_up_messages = [
            *full_messages,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    }
                }]
            },
            {"role": "tool", "tool_call_id": tool_call.id, "content": tool_result},
        ]

        final_msg = await call_groq(follow_up_messages, max_tokens=500)
        content = final_msg.content or "Booking confirmed!"

    else:
        content = message.content

        content = re.sub(r'<function=\w+>\s*\{.*?\}\s*</function>', '', content, flags=re.DOTALL).strip()
        content = re.sub(r'<function=\w+/>', '', content).strip()

    return JSONResponse({"response": content})