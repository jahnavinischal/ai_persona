import httpx
import os
from datetime import datetime, timedelta, timezone

CAL_BASE = "https://api.cal.com/v2"

def cal_headers(version="2024-09-04"):
    return {
        "Authorization": f"Bearer {os.getenv('CAL_API_KEY')}",
        "cal-api-version": version,
    }

# async def get_available_slots() -> list[str]:
#     now = datetime.now(timezone.utc)
#     end = now + timedelta(days=7)

#     async with httpx.AsyncClient() as client:
#         res = await client.get(
#             f"{CAL_BASE}/slots",
#             headers=cal_headers("2024-09-04"),
#             params={
#                 "eventTypeId": os.getenv("CAL_EVENT_TYPE_ID"),
#                 "start": now.strftime("%Y-%m-%d"),
#                 "end": end.strftime("%Y-%m-%d"),
#                 "timeZone": "Asia/Kolkata",
#             },
#             timeout=10,
#         )
#         res.raise_for_status()
#         data = res.json()

#     slots = data.get("data", {})
#     readable = []
#     for date, times in list(slots.items())[:3]:
#         for t in times[:2]:  # first 2 slots per day
#             iso = t["start"]
#             label = iso[0:10] + " at " + iso[11:16] + " IST"
#             readable.append(f"{label} (use slot: {iso})")

#     return readable or ["No slots available this week"]

# Global slot map — label -> ISO string
SLOT_MAP = {}

async def get_available_slots() -> list[str]:
    global SLOT_MAP
    SLOT_MAP = {}
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=7)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{CAL_BASE}/slots",
            headers=cal_headers("2024-09-04"),
            params={
                "eventTypeId": os.getenv("CAL_EVENT_TYPE_ID"),
                "start": now.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "timeZone": "Asia/Kolkata",
            },
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

    slots = data.get("data", {})
    readable = []
    for date, times in list(slots.items())[:3]:
        for t in times[:2]:
            iso = t["start"]
            label = iso[0:10] + " at " + iso[11:16] + " IST"
            SLOT_MAP[label] = iso
            readable.append(label)

    return readable or []

async def book_meeting(name: str, email: str, slot: str) -> dict:
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{CAL_BASE}/bookings",
            headers=cal_headers("2024-08-13"),
            json={
                "eventTypeId": int(os.getenv("CAL_EVENT_TYPE_ID")),
                "start": slot,
                "attendee": {
                    "name": name,
                    "email": email,
                    "timeZone": "Asia/Kolkata",
                    "language": "en",
                },
                "metadata": {},
            },
            timeout=10,
        )
        print("CAL RESPONSE:", res.status_code, res.text)
        res.raise_for_status()
        return res.json()