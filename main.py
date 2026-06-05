import json
import os
import httpx
import asyncio


from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from groq import AsyncGroq
from dotenv import load_dotenv

from persona import SYSTEM_PROMPT
from cal_tool import get_available_slots, book_meeting, SLOT_MAP

load_dotenv()
app = FastAPI()
groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

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


# async def run_tool(name: str, args: dict) -> str:
#     print("TOOL CALLED:", name, "ARGS:", args)  # add this line
    
#     if name == "get_availability":
#         slots = await get_available_slots()
#         return ", ".join(slots)

#     if name == "book_meeting":
#         try:
#             result = await book_meeting(**args)
#             data = result.get("data", result)
#             uid = data.get("uid")
#             if uid:
#                 return f"Booking confirmed! Confirmation sent to {args['email']}. Reference: {uid}"
#             return "Booking failed — please try again"
#         except Exception as e:
#             error_str = str(e)
#             if "already has booking" in error_str or "not available" in error_str:
#                 return "That slot is already taken. Let me get you other available slots."
#             return f"Booking failed: {error_str}"
async def run_tool(name: str, args: dict) -> str:
    print("TOOL CALLED:", name, "ARGS:", args)

    if name == "get_availability":
        slots = await get_available_slots()
        if not slots:
            return "No slots available this week."
        return "Available slots: " + " | ".join(slots)


    # if name == "book_meeting":
    #     try:
    #         result = await book_meeting(**args)
    #         data = result.get("data", result)
    #         uid = data.get("uid")
    #         if uid:
    #             return f"Booking confirmed! Reference ID: {uid}. Confirmation sent to {args['email']}."
    #         return f"Booking failed — response was: {result}"
    #     except httpx.HTTPStatusError as e:
    #         print("CAL ERROR:", e.response.status_code, e.response.text)
    #         body = e.response.text
    #         if "already" in body.lower() or "not available" in body.lower():
    #             return "That slot is just taken. Call get_availability again for fresh slots."
    #         return f"Booking failed ({e.response.status_code}): {body}"
    #     except Exception as e:
    #         print("UNEXPECTED ERROR:", type(e), e)
    #         return f"Booking error: {str(e)}"

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

async def call_groq(messages: list) -> dict:
    """One Groq call. Returns the message dict."""
    response = await groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=300,
        temperature=0.4,
    )
    return response.choices[0].message


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

        final = await groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=follow_up_messages,
            max_tokens=150,
            temperature=0.4,
        )

        final_content = final.choices[0].message.content or "Your interview has been booked successfully!"
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

    # Vapi sends other event types (call ended, transcript, etc.) — acknowledge them
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

    # Strip any system message Vapi sends — we control it
    messages = [m for m in messages if m.get("role") != "system"]

    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
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
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            },
        ]

        final = await groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=follow_up_messages,
            max_tokens=150,
            temperature=0.4,
        )
        content = final.choices[0].message.content or "Your interview has been booked successfully!"
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