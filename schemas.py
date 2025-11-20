"""
Database Schemas for BoomiisUK (MongoDB via Pydantic)

Each Pydantic model maps to a MongoDB collection using the lowercase
class name as the collection name.

Collections:
- user
- menucategory
- menuitem
- order
- orderitem
- reservation
- eventinquiry
- blogpost
- galleryimage
- subscriber
- sitesetting
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime

# Auth/User
class User(BaseModel):
    email: str = Field(..., description="Unique email address")
    name: str = Field(..., description="Full name")
    password_hash: str = Field(..., description="Hashed password")
    role: Literal["admin", "staff"] = Field("admin")
    is_active: bool = Field(True)

# Menu
class MenuCategory(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    position: int = 0
    is_active: bool = True

class MenuItem(BaseModel):
    title: str
    slug: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    currency: str = "GBP"
    category_slug: str
    image_url: Optional[str] = None
    tags: List[str] = []  # e.g., ["vegan", "halal", "gluten-free"]
    allergens: List[str] = []
    is_active: bool = True

# Orders
class OrderItem(BaseModel):
    item_slug: str
    title: str
    unit_price: float
    quantity: int = Field(..., ge=1)
    subtotal: float

class Order(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    order_type: Literal["pickup", "delivery"]
    address: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    items: List[OrderItem]
    notes: Optional[str] = None
    subtotal: float
    taxes: float
    fees: float
    total: float
    currency: str = "GBP"
    status: Literal["payment_required", "paid", "cancelled"] = "payment_required"
    payment_intent_id: Optional[str] = None
    payment_method: Optional[str] = None
    payment_ref: Optional[str] = None

# Reservations
class Reservation(BaseModel):
    full_name: str
    email: str
    phone: str
    date: str  # ISO date string (client validates)
    time: str  # HH:MM
    party_size: int = Field(..., ge=1, le=20)
    notes: Optional[str] = None
    status: Literal["requested", "confirmed", "declined", "cancelled"] = "requested"

# Events & Catering
class EventInquiry(BaseModel):
    full_name: str
    email: str
    phone: Optional[str] = None
    event_date: Optional[str] = None
    headcount: Optional[int] = None
    budget_range: Optional[str] = None
    message: Optional[str] = None
    status: Literal["new", "in_review", "responded", "closed"] = "new"

# Blog
class BlogPost(BaseModel):
    title: str
    slug: str
    excerpt: Optional[str] = None
    content: str
    cover_image_url: Optional[str] = None
    published: bool = True
    published_at: Optional[datetime] = None

# Gallery
class GalleryImage(BaseModel):
    title: Optional[str] = None
    image_url: str
    category: Optional[str] = None
    alt: Optional[str] = None
    position: int = 0
    is_active: bool = True

# Newsletter
class Subscriber(BaseModel):
    email: str
    name: Optional[str] = None
    source: Optional[str] = None

# Site settings
class SiteSetting(BaseModel):
    key: str
    value: dict | str | int | float | bool
    description: Optional[str] = None
