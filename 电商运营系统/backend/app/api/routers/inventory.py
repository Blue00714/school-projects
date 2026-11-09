"""OWNER: forecast-inventory — simulated inventory scenarios, ledger and replenishment.

Endpoints (all under /api):
  GET  /inventory/scenarios
  POST /inventory/scenarios                 {name,note?,stock_codes?}
  GET  /inventory/scenarios/{sid}/items
  PUT  /inventory/scenarios/{sid}/items/{stock_code}
  POST /inventory/scenarios/{sid}/movements {stock_code,kind,qty,idempotency_key?,note?}
  GET  /inventory/scenarios/{sid}/movements
  GET  /inventory/scenarios/{sid}/replenishment
  POST /inventory/scenarios/{sid}/replenishment/confirm
  GET  /inventory/scenarios/{sid}/in-transit
  POST /inventory/scenarios/{sid}/in-transit/{iid}/receive

All calculations live in ``app.services.inventory``; every response carries
``simulated: true`` because these quantities are manual what-if inputs, never real stock
and never derived from historical transactions.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import current_user
from app.services import inventory

router = APIRouter(tags=['inventory'])

DEFAULT_LIMIT = 200


class ScenarioBody(BaseModel):
    name: str
    note: str | None = None
    stock_codes: list[str] | None = None


class ItemBody(BaseModel):
    name: str | None = None
    initial_qty: float | None = None
    lead_time_days: int | None = None
    safety_stock: float | None = None
    daily_demand: float | None = None


class MovementBody(BaseModel):
    stock_code: str
    kind: str
    qty: float
    idempotency_key: str | None = None
    note: str | None = None


class ConfirmBody(BaseModel):
    stock_code: str
    qty: float
    expected_date: str
    idempotency_key: str | None = None


@router.get('/inventory/scenarios')
def read_scenarios(user: dict = Depends(current_user)):
    return {'scenarios': inventory.list_scenarios(), 'simulated': True}


@router.post('/inventory/demo')
def load_demo(user: dict = Depends(current_user)):
    return inventory.ensure_demo_scenario(user=user)


@router.post('/inventory/scenarios', status_code=201)
def create_scenario(payload: ScenarioBody, user: dict = Depends(current_user)):
    scenario = inventory.create_scenario(
        name=payload.name, note=payload.note, stock_codes=payload.stock_codes, user=user)
    return {'scenario': scenario, 'simulated': True}


@router.get('/inventory/scenarios/{scenario_id}/items')
def read_items(scenario_id: str, user: dict = Depends(current_user)):
    return inventory.items_payload(scenario_id)


@router.put('/inventory/scenarios/{scenario_id}/items/{stock_code}')
def update_item(scenario_id: str, stock_code: str, payload: ItemBody,
                user: dict = Depends(current_user)):
    return inventory.update_item(scenario_id, stock_code, payload.model_dump(exclude_unset=True))


@router.post('/inventory/scenarios/{scenario_id}/movements')
def create_movement(scenario_id: str, payload: MovementBody, user: dict = Depends(current_user)):
    result = inventory.create_movement(scenario_id, payload.model_dump(), user=user)
    # Idempotent replay answers 200 instead of 201 so clients can tell it apart.
    return JSONResponse(status_code=200 if result.get('duplicate') else 201, content=result)


@router.get('/inventory/scenarios/{scenario_id}/movements')
def read_movements(scenario_id: str, limit: int = Query(DEFAULT_LIMIT, ge=0),
                   user: dict = Depends(current_user)):
    return inventory.list_movements(scenario_id, limit)


@router.get('/inventory/scenarios/{scenario_id}/replenishment')
def read_replenishment(scenario_id: str, user: dict = Depends(current_user)):
    return inventory.replenishment(scenario_id)


@router.post('/inventory/scenarios/{scenario_id}/replenishment/confirm', status_code=201)
def confirm_replenishment(scenario_id: str, payload: ConfirmBody,
                          user: dict = Depends(current_user)):
    return inventory.confirm_replenishment(scenario_id, payload.model_dump(), user=user)


@router.get('/inventory/scenarios/{scenario_id}/in-transit')
def read_in_transit(scenario_id: str, user: dict = Depends(current_user)):
    return inventory.list_in_transit(scenario_id)


@router.post('/inventory/scenarios/{scenario_id}/in-transit/{transit_id}/receive')
def receive_in_transit(scenario_id: str, transit_id: str, user: dict = Depends(current_user)):
    return inventory.receive_in_transit(scenario_id, transit_id, user=user)
