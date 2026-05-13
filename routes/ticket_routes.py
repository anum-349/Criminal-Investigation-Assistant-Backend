# routes/ticket_routes.py
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from db import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.ticket_schema import (
    CreateTicketRequest, UpdateTicketRequest,
    AddReplyRequest, TicketOut, TicketListOut,
)
from services import ticket_service as svc

router = APIRouter()


@router.post("", response_model=TicketOut, status_code=201)
def create_ticket(
    body: CreateTicketRequest,
    request: Request,
    db: Session    = Depends(get_db),
    user: User     = Depends(get_current_user),
):
    return svc.create_ticket(db, user=user, body=body, request=request)


@router.get("", response_model=TicketListOut)
def list_tickets(
    request:       Request,
    page:          int = Query(1,     ge=1),
    page_size:     int = Query(10,    ge=1, le=100),
    status_filter: str = Query("all"),
    search:        str = Query(""),
    db:   Session  = Depends(get_db),
    user: User     = Depends(get_current_user),
):
    return svc.list_tickets(
        db, user=user, request=request,
        page=page, page_size=page_size,
        status_filter=status_filter, search=search,
    )


@router.get("/{ticket_id}", response_model=TicketOut)
def get_ticket(
    ticket_id: str,
    request:   Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.get_ticket(db, user=user, ticket_id=ticket_id, request=request)


@router.patch("/{ticket_id}", response_model=TicketOut)
def update_ticket(
    ticket_id: str,
    body:      UpdateTicketRequest,
    request:   Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.update_ticket(db, user=user, ticket_id=ticket_id, body=body, request=request)


@router.post("/{ticket_id}/replies", response_model=TicketOut)
def add_reply(
    ticket_id: str,
    body:      AddReplyRequest,
    request:   Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.add_reply(db, user=user, ticket_id=ticket_id, body=body, request=request)


@router.delete("/{ticket_id}")
def delete_ticket(
    ticket_id: str,
    request:   Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user),
):
    return svc.delete_ticket(db, user=user, ticket_id=ticket_id, request=request)