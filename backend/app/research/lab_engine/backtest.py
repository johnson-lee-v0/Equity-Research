"""Cash-funded event replay. Prices are synthetic total-return units, never actual shares.
No transaction-date execution, shorting, hidden drops, future Kelly training, or future prices.
"""
import bisect,collections,datetime as dt,math,statistics
from zoneinfo import ZoneInfo
from .cash_capacity import request_capacity, summarize_cash
END='2026-09-04'

def owner_key(row):
 return (row['owner'],str(row.get('dependentChildId') or ''))

def metrics(curve,initial=100000,annual_factor=252):
 vals=[x['nav'] for x in curve]; rets=[vals[0]/initial-1]+[vals[i]/vals[i-1]-1 for i in range(1,len(vals))];peak=initial;dd=0
 for v in vals:peak=max(peak,v);dd=min(dd,v/peak-1)
 yrs=max((dt.date.fromisoformat(curve[-1]['date'])-dt.date.fromisoformat(curve[0]['date'])).days/365.25,1/365.25)
 vol=statistics.stdev(rets)*math.sqrt(annual_factor) if len(rets)>1 else 0
 return {'endingValue':vals[-1],'totalReturn':vals[-1]/initial-1,'cagr':(vals[-1]/initial)**(1/yrs)-1,'maxDrawdown':dd,'volatility':vol,'sharpe':statistics.mean(rets)*annual_factor/vol if vol else None,'averageInvested':statistics.mean(x['invested'] for x in curve),'benchmarkReturn':curve[-1]['benchmark']/initial-1}

def kelly(history,day):
 """Training ledger is a fixed-size shadow portfolio; every outcome predates execution.
 Cluster same-day exits before fitting log utility to avoid counting a batch as independent bets.
 Conservative 1% pilot before 30 exit dates and 5 losses. Empirical estimate is a diagnostic.
 """
 prior=[x for x in history if x['date']<day and x['date']>=str(int(day[:4])-3)+day[4:]]
 byday=collections.defaultdict(list)
 for x in prior:byday[x['date']].append(x['return'])
 rs=[statistics.mean(v) for v in byday.values()];n=len(rs);losses=sum(r<0 for r in rs)
 if n<30 or losses<5:return {'fraction':None,'observations':n,'losses':losses,'reason':'Pilot: fewer than 30 exit days or 5 losing days'}
 scores=[]
 for i in range(101):
  f=i/100;s=sum(math.log1p(f*r) if f*r>-1 else -1e9 for r in rs)/n;scores.append(s)
 best=max(range(len(scores)),key=lambda i:scores[i]);return {'fraction':best/100,'observations':n,'losses':losses,'reason':'Prior closed outcomes, clustered by exit day'}

class Engine:
 def __init__(self,rows,prices,price_policies=None):
  self.price_policies=price_policies or {}
  self.rows=rows;self.prices=prices;self.stock_calendar=sorted(prices['SPY']);self.crypto={r['priceSymbol'] for r in rows if r.get('assetClass')=='crypto' and r['eligible']};self.close={};self.previous_close={};self.lastdate={}
  first=dt.date.fromisoformat(self.stock_calendar[0]);last=dt.date.fromisoformat(self.stock_calendar[-1]);self.calendar=[(first+dt.timedelta(days=i)).isoformat() for i in range((last-first).days+1)] if self.crypto else self.stock_calendar
  symbols={r['priceSymbol'] for r in rows if r['eligible']}|{'SPY'}
  for s in symbols:
   bars=prices.get(s,{});last=None;ld=None;self.close[s]={};self.previous_close[s]={};self.lastdate[s]={}
   for d in self.calendar:
    if last is not None:self.previous_close[s][d]=last
    if d in bars:last=bars[d]['close'];ld=d
    if last is not None:self.close[s][d]=last;self.lastdate[s][d]=ld
 def schedule(self,rows,start,delay):
  events=collections.defaultdict(list);excluded=[]
  for r in rows:
   reason=r['exclusion']
   if not r['eligible']:excluded.append({'id':r['id'],'reason':reason});continue
   if r['priceSymbol'] not in self.prices:excluded.append({'id':r['id'],'reason':'Price history unavailable'});continue
   market_calendar=self.calendar if r['priceSymbol'] in self.crypto else self.stock_calendar
   is_crypto=r['priceSymbol'] in self.crypto
   zone=dt.timezone.utc if is_crypto else ZoneInfo('America/New_York')
   clock='T00:00:00' if is_crypto else 'T09:30:00'
   stamp=r.get('firstPublicAt') or r.get('filedAt')
   if stamp:
    public=dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))
    if public.tzinfo is None:public=public.replace(tzinfo=ZoneInfo('America/New_York'))
    eligible_at=public.astimezone(dt.timezone.utc)+dt.timedelta(days=delay)
    target=eligible_at.astimezone(zone).date().isoformat();i=bisect.bisect_left(market_calendar,target)
    while i<len(market_calendar) and dt.datetime.fromisoformat(market_calendar[i]+clock).replace(tzinfo=zone)<eligible_at:i+=1
   else:
    target=(dt.date.fromisoformat(r['filedDate'])+dt.timedelta(days=delay)).isoformat();i=bisect.bisect_left(market_calendar,target)
   if i>=len(market_calendar):excluded.append({'id':r['id'],'reason':'Execution after price cutoff'});continue
   day=market_calendar[i]
   if day<start:continue
   if day not in self.prices[r['priceSymbol']]:excluded.append({'id':r['id'],'reason':'No price on scheduled execution session'});continue
   events[day].append(r)
  return events,excluded
 def simulate(self,rows,start='2020-01-02',delay=1,sizing='fixed_2',history=None,end=None,fee_bps=10):
  cost_rate = fee_bps / 10000
  cutoff=end or END
  calendar=[d for d in self.calendar if start<=d<=cutoff];events,excluded=self.schedule(rows,start,delay)
  cash=100000.;positions={};orders=[];closed=[];curve=[];fees=0;unmatched=0;scaled=0;skipped=0;turnover=0
  if not calendar:
   curve=[{'date':cutoff,'nav':100000.,'benchmark':100000.,'cash':100000.,'invested':0.}]
   return {'valuationStatus':'cash_before_first_session','metrics':metrics(curve),'curve':curve,'orders':[],'closed':[],'holdings':[],'exclusions':excluded,'fees':0,'turnover':0,'unmatchedSales':0,'scaledBuys':0,'skippedBuys':0,'recordCount':len(rows),'executedBuys':0,'staleValue':0,'kellyNow':kelly([],cutoff)}
  first_stock_day=next((d for d in self.stock_calendar if start<=d<=cutoff),None)
  sp0=self.prices['SPY'][first_stock_day]['open'] if first_stock_day else 1.;cachekelly={}
  for day in calendar:
   def mark(p,opening=False):
    s=p['symbol'];bar=self.prices.get(s,{}).get(day)
    if opening:
     if market=='crypto' and s not in self.crypto:return self.previous_close.get(s,{}).get(day,p['entryPrice'])
     return bar['open'] if bar else self.previous_close.get(s,{}).get(day,p['entryPrice'])
    return self.close.get(s,{}).get(day,p['entryPrice'])
   for market in (['crypto','equity'] if self.crypto else ['equity']):
    today=sorted([r for r in events.get(day,[]) if (r['priceSymbol'] in self.crypto)==(market=='crypto')],key=lambda r:r['id'])
    if not today and sizing!='quarter_kelly':continue
    for r in [r for r in today if r['action']=='sell']:
     key=(r['politicianId'],owner_key(r),r['account'] or 'Unknown account',r['priceSymbol']);p=positions.get(key)
     if not p:
      unmatched+=1;orders.append({'id':r['id'],'date':day,'executionSession':market,'action':'sell','status':'Unmatched sale','notional':0});continue
     price=mark(p,True);gross=p['units']*price;fee=gross*cost_rate;cash+=gross-fee;fees+=fee;turnover+=gross
     outcome={'date':day,'entryDate':p['firstDate'],'politicianId':r['politicianId'],'ticker':r['ticker'],'owner':r['owner'],'account':r['account'],'return':(gross-fee)/p['cost']-1,'pnl':gross-fee-p['cost'],'cost':p['cost'],'exitId':r['id']};closed.append(outcome)
     orders.append({'id':r['id'],'date':day,'executionSession':market,'action':'sell','status':'Executed: exit on any sale','notional':gross,'fee':fee,'return':outcome['return']});del positions[key]
    nav=cash+sum(p['units']*mark(p,True) for p in positions.values())
    buys=[r for r in today if r['action']=='buy'];requests=[]
    if sizing=='quarter_kelly':
     month=day[:7]
     if month not in cachekelly:cachekelly[month]=kelly(history or [],day)
     diagnostic=cachekelly[month];fraction=min(.05,.25*diagnostic['fraction']) if diagnostic['fraction'] is not None else .01
    else:fraction={'fixed_1':.01,'fixed_2':.02,'fixed_5':.05}[sizing];diagnostic=None
    current=collections.defaultdict(float)
    for p in positions.values():current[p['symbol']]+=p['units']*mark(p,True)
    counts=collections.Counter(r['priceSymbol'] for r in buys)
    for r in buys:
     s=r['priceSymbol'];headroom=max(0,nav*.10-current[s]);wanted=min(nav*fraction,headroom/counts[s]);requests.append((r,wanted))
    total=sum(x[1] for x in requests)*(1+cost_rate);budget=cash;scale=min(1,cash/total) if total else 0
    for r,wanted in requests:
     gross=wanted*scale
     capacity=request_capacity(nav*fraction,wanted,gross,budget,scale)
     if gross<1:
      skipped+=1;orders.append({'id':r['id'],'date':day,'executionSession':market,'action':'buy','status':'No cash, no Kelly edge or ticker cap','notional':0,'capacity':capacity});continue
     fee=gross*cost_rate;price=self.prices[r['priceSymbol']][day]['open'];cash-=gross+fee;fees+=fee;turnover+=gross
     key=(r['politicianId'],owner_key(r),r['account'] or 'Unknown account',r['priceSymbol'])
     p=positions.setdefault(key,{'symbol':r['priceSymbol'],'ticker':r['ticker'],'politicianId':r['politicianId'],'owner':r['owner'],'dependentChildId':r.get('dependentChildId'),'account':r['account'],'units':0.,'cost':0.,'entryPrice':price,'firstDate':day});p['units']+=gross/price;p['cost']+=gross+fee
     was_scaled=scale<.999999 or wanted<nav*fraction-.01;scaled+=int(was_scaled)
     orders.append({'id':r['id'],'date':day,'executionSession':market,'action':'buy','status':'Scaled to cash / ticker cap' if was_scaled else 'Executed','notional':gross,'fee':fee,'targetWeight':fraction,'navBefore':nav,'kelly':diagnostic,'capacity':capacity})
   assert cash>=-.0001,('negative cash',cash,day)
   value=sum(p['units']*mark(p) for p in positions.values());nav=cash+value
   assert all(p['units']>=0 for p in positions.values()) and math.isfinite(nav)
   curve.append({'date':day,'nav':round(nav,4),'benchmark':round(100000*self.close['SPY'][day]/sp0,4) if first_stock_day and day>=first_stock_day else 100000.,'cash':round(cash,4),'invested':value/nav})
  holdings=[]
  for p in positions.values():
   d=self.lastdate[p['symbol']].get(calendar[-1]);price=self.close[p['symbol']].get(calendar[-1],p['entryPrice']);value=price*p['units'];stale=(dt.date.fromisoformat(calendar[-1])-dt.date.fromisoformat(d)).days>7
   policy=self.price_policies.get(p['symbol'],{});boundary=policy.get('lastTradableSession');corporate_action=bool(boundary and calendar[-1]>boundary)
   holdings.append({**p,'value':value,'pnl':value-p['cost'],'lastPriceDate':d,'stale':stale or corporate_action,'valuationRequiresCorporateAction':corporate_action,'settlementStatus':policy.get('settlementStatus') if corporate_action else None,'priceIsCurrentMarketQuote':not (stale or corporate_action)})
  result={'valuationStatus':'incomplete' if any(p['stale'] for p in holdings) else 'current_marks','metrics':metrics(curve,annual_factor=365.25 if self.crypto else 252),'curve':curve,'orders':orders,'closed':closed,'holdings':sorted(holdings,key=lambda p:-p['value']),'exclusions':excluded,'fees':fees,'turnover':turnover/100000,'unmatchedSales':unmatched,'scaledBuys':scaled,'skippedBuys':skipped,'recordCount':len(rows),'executedBuys':sum(o['action']=='buy' and o['notional']>0 for o in orders),'staleValue':sum(p['value'] for p in holdings if p['stale']),'kellyNow':kelly(history or closed,cutoff if end else '2026-09-07')}
  result['cashDiagnostics']=summarize_cash(result)
  return result

