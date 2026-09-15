from sqlalchemy import (
    Column, Integer, String, Text, Float, Date, DateTime,
    ForeignKey, Boolean, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Customers(Base):
    __tablename__ = "customers"

    customer_id = Column(Integer, primary_key=True)
    first_name = Column(String(80))
    last_name = Column(String(80))
    email = Column(String(120))
    phone = Column(String(30))
    address = Column(Text)
    preferences = Column(Text)
    nationality = Column(String(80))
    language = Column(String(40))
    loyalty_tier = Column(String(20))

    bookings = relationship("RoomBookings", back_populates="customer")


class Rooms(Base):
    __tablename__ = "rooms"

    room_id = Column(String(10), primary_key=True)
    room_number = Column(Integer, unique=True, nullable=False, index=True)
    floor = Column(Integer)
    type = Column(String(50))
    square_feet = Column(Integer)
    basic_amenities = Column(Text)
    additional_amenities = Column(Text)
    max_occupancy = Column(Integer)
    bed_type = Column(String(50))
    view_type = Column(String(50))
    accessibility = Column(String(100))
    status = Column(String(30))
    last_renovation = Column(Date)
    base_rate = Column(Integer)
    max_rate = Column(Integer)

    availability = relationship("RoomAvailability", back_populates="room")


class RoomBookings(Base):
    __tablename__ = "room_bookings"

    booking_id = Column(String(10), primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    room_number = Column(Integer, ForeignKey("rooms.room_number"))
    room_type = Column(String(50))
    check_in = Column(DateTime)
    check_out = Column(DateTime)
    duration_days = Column(Integer)
    num_adults = Column(Integer)
    num_children = Column(Integer)
    loyalty_tier = Column(String(20))
    special_amenities = Column(Text)
    special_requests = Column(Text)
    booking_status = Column(String(30))
    payment_method = Column(String(30))
    total_amount = Column(Float)
    points_earned = Column(Integer)

    customer = relationship("Customers", back_populates="bookings")


class RoomAvailability(Base):
    __tablename__ = "room_availability"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(10), ForeignKey("rooms.room_id"))
    room_number = Column(Integer)
    date = Column(Date, nullable=False, index=True)
    status = Column(String(30))
    price = Column(Float)
    max_occupancy = Column(Integer)

    room = relationship("Rooms", back_populates="availability")

    __table_args__ = (
        UniqueConstraint("room_id", "date", name="uq_room_date"),
    )


class Amenities(Base):
    __tablename__ = "amenities"

    amenity_id = Column(String(10), primary_key=True)
    category = Column(String(80))
    name = Column(String(80))
    price = Column(Integer)
    duration = Column(Integer)
    description = Column(Text)
    availability = Column(String(40))
    location = Column(Text)
    booking_required = Column(Boolean)
    min_notice_hours = Column(Integer)


class Promotions(Base):
    __tablename__ = "promotions"

    promotion_id = Column(String(10), primary_key=True)
    name = Column(String(30))
    description = Column(Text)
    discount_type = Column(String(30))
    discount_value = Column(Integer)
    min_stay = Column(Integer)
    applicable_room_types = Column(String(100))
    start_date = Column(Date)
    end_date = Column(Date)
    blackout_dates = Column(Text)
    terms_conditions = Column(Text)
    booking_code = Column(String(10))
    status = Column(String(20))


class RecommendationsKnowledgeBase(Base):
    __tablename__ = "recommendations_knowledge_base"

    recommendation_id = Column(String(10), primary_key=True)
    category = Column(String(30))
    name = Column(String(100))
    description = Column(Text)
    address = Column(Text)
    distance_km = Column(Float)
    price_range = Column(String(30))
    rating = Column(Float)
    review_count = Column(Integer)
    booking_required = Column(Boolean)
    seasonal = Column(Boolean)
    tags = Column(Text)
    keywords = Column(Text)
    last_verified = Column(Date)
 

class FAQKnowledgeBase(Base):
    __tablename__ = "faq_knowledge_base"

    faq_id = Column(String(10), primary_key=True)
    category = Column(String(30))
    subcategory = Column(String(30))
    question = Column(Text)
    answer = Column(Text)
    keywords = Column(Text)
    last_updated = Column(Date)
    helpful_votes = Column(Integer)
    views = Column(Integer)