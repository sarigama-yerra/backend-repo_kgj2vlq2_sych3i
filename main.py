import os
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import (
    User,
    MenuCategory,
    MenuItem,
    Order,
    OrderItem,
    Reservation,
    EventInquiry,
    BlogPost,
    GalleryImage,
    Subscriber,
    SiteSetting,
)

import hashlib
import hmac
import secrets

app = FastAPI(title="BoomiisUK API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------
# Auth (very simple session cookie)
# -----------------
SESSION_COOKIE = "boom_admin"
SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", "dev-secret-change")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@boomiis.uk")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")  # store sha256 hash

class LoginRequest(BaseModel):
    email: str
    password: str


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password), hashed)


def require_admin(session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Simple HMAC verification
    try:
        email, sig = session.split(":", 1)
        expected = hmac.new(SESSION_SECRET.encode(), msg=email.encode(), digestmod=hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad sig")
        if email != ADMIN_EMAIL:
            raise ValueError("not admin")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session")


@app.post("/api/admin/login")
def admin_login(payload: LoginRequest, resp: Response):
    if not ADMIN_PASSWORD_HASH:
        # allow first-time bootstrap with ENV ADMIN_PASSWORD
        admin_plain = os.getenv("ADMIN_PASSWORD", "admin12345")
        os.environ["ADMIN_PASSWORD_HASH"] = hash_password(admin_plain)
    hashed = os.getenv("ADMIN_PASSWORD_HASH")
    if payload.email != ADMIN_EMAIL or not verify_password(payload.password, hashed):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    sig = hmac.new(SESSION_SECRET.encode(), msg=ADMIN_EMAIL.encode(), digestmod=hashlib.sha256).hexdigest()
    cookie_val = f"{ADMIN_EMAIL}:{sig}"
    resp.set_cookie(SESSION_COOKIE, cookie_val, httponly=True, secure=False, samesite="lax", max_age=60*60*8)
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(resp: Response):
    resp.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


# -----------------
# Utility
# -----------------
@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_name"] = getattr(db, 'name', 'unknown')
            collections = db.list_collection_names()
            response["collections"] = collections[:20]
            response["database"] = "✅ Connected & Working"
            response["connection_status"] = "Connected"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:120]}"
    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


# -----------------
# Public Content Endpoints
# -----------------
@app.get("/api/menu")
def get_menu(tag: Optional[str] = None, category: Optional[str] = None):
    q = {}
    if tag:
        q["tags"] = {"$in": [tag]}
    if category:
        q["category_slug"] = category
    items = get_documents("menuitem", q)
    cats = get_documents("menucategory")
    return {"categories": cats, "items": items}


@app.get("/api/blog")
def blog_list():
    posts = get_documents("blogpost", {"published": True})
    posts.sort(key=lambda p: p.get("published_at") or datetime.utcnow(), reverse=True)
    return posts


@app.get("/api/blog/{slug}")
def blog_detail(slug: str):
    docs = get_documents("blogpost", {"slug": slug, "published": True}, limit=1)
    if not docs:
        raise HTTPException(status_code=404, detail="Not found")
    return docs[0]


@app.get("/api/gallery")
def gallery_list():
    images = get_documents("galleryimage", {"is_active": True})
    images.sort(key=lambda x: x.get("position", 0))
    return images


class SubscribeBody(BaseModel):
    email: str
    name: Optional[str] = None


@app.post("/api/subscribe")
def subscribe(body: SubscribeBody):
    # naive uniqueness by email
    existing = get_documents("subscriber", {"email": body.email}, limit=1)
    if existing:
        return {"ok": True}
    create_document("subscriber", body.model_dump())
    return {"ok": True}


# -----------------
# Orders + Stripe
# -----------------
class CreateOrderBody(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    order_type: str
    address: Optional[str] = None
    scheduled_for: Optional[str] = None
    items: List[OrderItem]
    notes: Optional[str] = None


def calculate_totals(items: List[OrderItem]):
    subtotal = sum(i.unit_price * i.quantity for i in items)
    taxes = round(subtotal * 0.0, 2)  # adjust if VAT to be shown separately
    fees = 0.0
    total = round(subtotal + taxes + fees, 2)
    return subtotal, taxes, fees, total


@app.post("/api/orders")
def create_order(body: CreateOrderBody):
    subtotal, taxes, fees, total = calculate_totals(body.items)
    order_doc = Order(
        full_name=body.full_name,
        email=body.email,
        phone=body.phone,
        order_type=body.order_type,  # pickup|delivery
        address=body.address,
        scheduled_for=None,
        items=body.items,
        notes=body.notes,
        subtotal=subtotal,
        taxes=taxes,
        fees=fees,
        total=total,
        currency="GBP",
    ).model_dump()

    stripe_secret = os.getenv("STRIPE_SECRET")
    if stripe_secret:
        try:
            import stripe  # type: ignore
            stripe.api_key = stripe_secret
            intent = stripe.PaymentIntent.create(
                amount=int(total * 100),
                currency="gbp",
                automatic_payment_methods={"enabled": True},
                metadata={"site": "BoomiisUK"},
            )
            order_doc["payment_intent_id"] = intent["id"]
            order_doc["status"] = "payment_required"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)[:120]}")

    order_id = create_document("order", order_doc)
    return {"order_id": order_id, "client_secret": order_doc.get("payment_intent_id")}


class ConfirmBody(BaseModel):
    order_id: str
    payment_ref: Optional[str] = None


@app.post("/api/orders/confirm")
def confirm_order(body: ConfirmBody):
    # Minimal confirmation stub (would normally verify PaymentIntent status via Stripe webhook or fetch)
    # For MVP: mark as paid when a payment_ref provided
    from bson import ObjectId
    if not body.order_id:
        raise HTTPException(status_code=400, detail="Missing order_id")
    try:
        oid = ObjectId(body.order_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    try:
        db["order"].update_one({"_id": oid}, {"$set": {"status": "paid", "payment_ref": body.payment_ref, "updated_at": datetime.utcnow()}})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


# -----------------
# Reservations
# -----------------
class ReservationBody(BaseModel):
    full_name: str
    email: str
    phone: str
    date: str
    time: str
    party_size: int
    notes: Optional[str] = None


@app.post("/api/reservations")
def create_reservation(body: ReservationBody):
    # Basic availability placeholder: limit to 20 seats per 30 minutes slot
    slot_key = f"{body.date} {body.time}"
    existing = db["reservation"].count_documents({"date": body.date, "time": body.time, "status": {"$in": ["requested", "confirmed"]}})
    if existing >= 20:
        raise HTTPException(status_code=409, detail="Fully booked for this time slot")
    res_id = create_document("reservation", Reservation(**body.model_dump()).model_dump())
    return {"reservation_id": res_id}


# -----------------
# Events inquiries
# -----------------
class EventInquiryBody(BaseModel):
    full_name: str
    email: str
    phone: Optional[str] = None
    event_date: Optional[str] = None
    headcount: Optional[int] = None
    budget_range: Optional[str] = None
    message: Optional[str] = None


@app.post("/api/events/inquiry")
def new_inquiry(body: EventInquiryBody):
    inq_id = create_document("eventinquiry", EventInquiry(**body.model_dump()).model_dump())
    return {"inquiry_id": inq_id}


# -----------------
# Admin CRUD (minimal)
# -----------------
@app.get("/api/admin/menu", dependencies=[Depends(require_admin)])
def admin_menu():
    return {
        "categories": get_documents("menucategory"),
        "items": get_documents("menuitem"),
    }


class UpsertCategory(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    position: int = 0
    is_active: bool = True


@app.post("/api/admin/menu/category", dependencies=[Depends(require_admin)])
def upsert_category(cat: UpsertCategory):
    db["menucategory"].update_one({"slug": cat.slug}, {"$set": cat.model_dump()}, upsert=True)
    return {"ok": True}


class UpsertItem(BaseModel):
    title: str
    slug: str
    description: Optional[str] = None
    price: float
    currency: str = "GBP"
    category_slug: str
    image_url: Optional[str] = None
    tags: List[str] = []
    allergens: List[str] = []
    is_active: bool = True


@app.post("/api/admin/menu/item", dependencies=[Depends(require_admin)])
def upsert_item(item: UpsertItem):
    db["menuitem"].update_one({"slug": item.slug}, {"$set": item.model_dump()}, upsert=True)
    return {"ok": True}


# -----------------
# Schema endpoint for viewer tooling
# -----------------
@app.get("/schema")
def get_schema_definitions():
    return {
        "collections": [
            "user","menucategory","menuitem","order","orderitem","reservation","eventinquiry","blogpost","galleryimage","subscriber","sitesetting"
        ]
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
