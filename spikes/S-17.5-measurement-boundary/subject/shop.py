"""A subject with an N+1 in it, small enough to run anywhere.

Deliberately *cheap*: the whole point of the measurement below is the ratio
between what the endpoint costs and what standing an interpreter up costs, and a
subject that took a second to answer would hide the effect being measured. This
one answers in about a millisecond, which is the regime `04-cost.md` and S-0.4
both describe for a real endpoint under a small fixture.
"""

from flask import Flask, jsonify
from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "ticket"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    followups: Mapped[list["Followup"]] = relationship(back_populates="ticket")


class Followup(Base):
    __tablename__ = "followup"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("ticket.id"))
    ticket: Mapped["Ticket"] = relationship(back_populates="followups")


engine = create_engine("sqlite:///subject.db")


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/tickets")
    def list_tickets() -> object:
        with Session(engine) as session:
            tickets = session.scalars(select(Ticket)).all()
            return jsonify([{"id": t.id, "followups": len(t.followups)} for t in tickets])

    return app
