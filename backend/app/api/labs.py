"""Bounded routes for consolidated strategy and congressional research."""
from __future__ import annotations
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from ..research.labs import LabsService
from ..research.lab_engine.research_trades import TradeIndexError


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy: Literal['rotation_20','next_high','legacy_open','filing_high','next_high_below_low'] = 'rotation_20'
    politician: str = Field(default='all', max_length=20000)
    chamber: Literal['all','house','senate'] = 'all'
    start: date = date(2020,1,2)
    end: date = date(2026,9,4)
    sizing: Literal['fixed_1','fixed_2','fixed_5','quarter_kelly'] = 'fixed_2'
    delay: Literal[1,31] = 1
    add_purchases: bool = True
    short_holding: bool = False
    exit_price: Literal['next_low','hold'] = 'next_low'
    fee_bps: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)


class LabImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    refresh: bool = False


def create_labs_router(config, write_dependency=None):
    service = LabsService(config)
    router = APIRouter(prefix='/api/labs', tags=['research labs'])
    def invoke(method, *args, **kwargs):
        try:
            with service.lock:
                return method(*args, **kwargs)
        except KeyError: raise HTTPException(404, 'Research item not found') from None
        except ValueError as exc: raise HTTPException(400, str(exc)) from None
        except TradeIndexError as exc: raise HTTPException(exc.status, str(exc)) from None
    def filters(chamber:Literal['all','house','senate']='all', politician:str=Query('all',max_length=20000), owner:Literal['all','Self','Spouse','Joint','Dependent','Unknown']='all', action:Literal['all','buy','sell','other']='all', date:date|None=None, search:str=Query('',max_length=200), eligible:bool|None=None):
        return dict(chamber=chamber, politician=politician, owner=owner, action=action, date=date.isoformat() if date else service.data.manifest().get('snapshotDate','9999-12-31'), search=search, eligible=eligible)
    @router.post('/import', dependencies=[Depends(write_dependency)] if write_dependency else [])
    def import_data(body:LabImportRequest): return invoke(service.import_data, body.refresh)
    @router.get('/catalog')
    def catalog(): return invoke(service.catalog)
    @router.get('/congress/records')
    def records(query=Depends(filters), offset:int=Query(0,ge=0), limit:int=Query(40,ge=1,le=100)):
        return invoke(service.records, offset=offset, limit=limit, **query)
    @router.get('/congress/export.csv')
    def export(query=Depends(filters)):
        return Response(invoke(service.export_csv, **query), media_type='text/csv', headers={'Content-Disposition':'attachment; filename="congress-filtered-records.csv"','Cache-Control':'no-store'})
    @router.get('/congress/records/{identifier:path}')
    def record(identifier:str): return invoke(service.record, identifier)
    @router.get('/congress/positions')
    def positions(query=Depends(filters), offset:int=Query(0,ge=0), limit:int=Query(40,ge=1,le=100), status:str=Query('all',max_length=80)):
        return invoke(service.positions, offset=offset, limit=limit, status=status, **query)
    @router.get('/congress/watch')
    def watch(): return invoke(service.watch)
    @router.get('/research/{identifier}')
    def research(identifier:str): return invoke(service.research, identifier)
    @router.get('/research-trades')
    def trades(case:str, date:date): return invoke(service.research_trades, case, date.isoformat())
    @router.get('/backtests')
    def history(): return invoke(service.history)
    @router.get('/backtests/{identifier}')
    def result(identifier:str): return invoke(service.result, identifier)
    @router.post('/backtests', dependencies=[Depends(write_dependency)] if write_dependency else [])
    def backtest(body:BacktestRequest): return invoke(service.backtest, body.model_dump(mode='json'))
    return router
