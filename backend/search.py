"""Small, public-profile searches. No scraping, private profiles or tree writes."""
import asyncio
import copy
import re
import os
import ssl
import time
from urllib.parse import urlencode, quote
import httpx
from pydantic import BaseModel, Field, model_validator

class SearchBody(BaseModel):
    first_name: str = Field(default='', max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    variants: str = Field(default='', max_length=300)
    birth_year: int | None = Field(default=None, ge=1, le=2100)
    death_year: int | None = Field(default=None, ge=1, le=2100)
    spread: int = Field(default=5, ge=1, le=20)
    place: str = Field(default='', max_length=200)
    father_first: str = Field(default='', max_length=100)
    father_last: str = Field(default='', max_length=100)
    mother_first: str = Field(default='', max_length=100)
    mother_last: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def clean(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, str): setattr(self, name, value.strip())
        if not self.last_name: raise ValueError('Enter a surname.')
        if len([v for v in self.variants.split(',') if v.strip()]) > 2:
            raise ValueError('Use up to two additional surname spellings.')
        return self

def website_links(q):
    name = ' '.join(filter(None, (q.first_name, q.last_name)))
    return [
        {'name':'FamilySearch', 'url':'https://www.familysearch.org/search/record/results?'+urlencode({'q.givenName':q.first_name,'q.surname':q.last_name}), 'note':'Historical records; free account. Some images have access restrictions. Names prefilled; add dates, places and relatives on the site.'},
        {'name':'FreeBMD','url':'https://www.freebmd.org.uk/search','note':'England and Wales birth, marriage and death indexes. Enter your details on the site.'},
        {'name':'FreeREG','url':'https://www.freereg.org.uk/','note':'Transcribed parish registers. Enter your details on the site.'},
        {'name':'FreeCEN','url':'https://www.freecen.org.uk/','note':'Transcribed census records. Enter your details on the site.'},
        {'name':'FIBIS','url':'https://search.fibis.org/bin/index.php','note':'Free name database for British India research. Enter your details on the site.'},
        {'name':'The National Archives','url':'https://discovery.nationalarchives.gov.uk/results/r?'+urlencode({'_q':name}), 'note':'Archive catalogue descriptions; names prefilled. Copies or downloads may cost money.'},
    ]

def assess(p,q):
    reasons=[]; conflicts=[]; unknown=[]
    for label,key,wanted in [('First name','FirstName',q.first_name),('Surname','LastNameAtBirth',q.last_name)]:
        if not wanted: continue
        actual=p.get(key,'')
        if not actual: unknown.append(label+' not supplied')
        elif actual.casefold()==wanted.casefold(): reasons.append(label+' matches entered text')
        else: conflicts.append(label+f' differs: source says {actual}; check whether this is a spelling variant')
    for label,key,wanted in [('Birth','BirthDate',q.birth_year),('Death','DeathDate',q.death_year)]:
        if wanted is None: continue
        value=p.get(key,'')
        year=int(value[:4]) if re.match(r'^\d{4}',value) else 0
        if not year: unknown.append(label+' year not supplied')
        elif abs(year-wanted)<=q.spread: reasons.append(f'{label} year {year} within ±{q.spread} years')
        else: conflicts.append(f'{label} year {year} outside entered range')
    if q.place:
        place=p.get('BirthLocation','')
        if not place: unknown.append('Birthplace not supplied')
        elif q.place.casefold() in place.casefold(): reasons.append('Birthplace contains entered place')
        else: conflicts.append('Birthplace differs; check historical names and boundaries')
    if any((q.father_first,q.father_last,q.mother_first,q.mother_last)):
        unknown.append('Parent names used in provider search; relationships must be checked on the source profile')
    return reasons,conflicts,unknown

class SearchService:
    def __init__(self, transport=None):
        self.transport=transport
        self.lock=asyncio.Lock()
        self.cache={}
        self.next_request=0
        self.cooldown=0

    async def search(self,q):
        key=q.model_dump_json()
        async with self.lock:
            now=time.monotonic()
            self.cache={k:v for k,v in self.cache.items() if v[0]>now}
            if key in self.cache:
                result=copy.deepcopy(self.cache[key][1]); result['cached']=True; return result
            if now<self.cooldown:
                return self.failure(q,'WikiTree requested a pause. Try again in a few minutes or open a website search.')
            results=[]; seen=set(); totals=[]; warnings=[]
            surnames=list(dict.fromkeys([q.last_name]+[v.strip() for v in q.variants.split(',') if v.strip()]))
            try:
                async with httpx.AsyncClient(timeout=20, transport=self.transport, trust_env=False, verify=ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE')), proxy=(os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')) if self.transport is None else None, headers={'User-Agent':'BlarmGenealogy/1 personal research'}) as client:
                    for surname in surnames:
                        await asyncio.sleep(max(0,self.next_request-time.monotonic()))
                        self.next_request=time.monotonic()+2
                        params={'action':'searchPerson','appId':'BlarmGenealogy','LastName':surname,'limit':25,'dateInclude':'neither','dateSpread':q.spread,'fields':'Id,Name,FirstName,LastNameAtBirth,LastNameCurrent,BirthDate,DeathDate,BirthLocation,DeathLocation'}
                        for field,value in [('FirstName',q.first_name),('BirthLocation',q.place),('fatherFirstName',q.father_first),('fatherLastName',q.father_last),('motherFirstName',q.mother_first),('motherLastName',q.mother_last)]:
                            if value: params[field]=value
                        if q.birth_year: params['BirthDate']=f'{q.birth_year:04d}-00-00'
                        if q.death_year: params['DeathDate']=f'{q.death_year:04d}-00-00'
                        response=await client.get('https://api.wikitree.com/api.php',params=params)
                        if response.status_code==429:
                            self.cooldown=time.monotonic()+300
                        response.raise_for_status()
                        payload=response.json()
                        if not isinstance(payload,list) or not payload or str(payload[0].get('status'))!='0':
                            raise ValueError('Unexpected provider response')
                        block=payload[0]; totals.append({'surname':surname,'total':block.get('total',0)})
                        for p in block.get('matches',[]):
                            name=p.get('Name')
                            if not name:
                                warnings.append('Some restricted profiles supplied no public details and were omitted.'); continue
                            if name in seen: continue
                            seen.add(name)
                            reasons,conflicts,unknown=assess(p,q)
                            results.append({'id':name,'name':' '.join(filter(None,[p.get('FirstName'),p.get('LastNameAtBirth')])) or name,
                                'birth':p.get('BirthDate',''),'death':p.get('DeathDate',''),'birthplace':p.get('BirthLocation',''),'deathplace':p.get('DeathLocation',''),
                                'url':'https://www.wikitree.com/wiki/'+quote(name,safe=''), 'source':'WikiTree','kind':'Contributed tree profile',
                                'reasons':reasons,'conflicts':conflicts,'unknown':unknown})
            except (httpx.HTTPError,ValueError,TypeError,KeyError):
                failed=self.failure(q,'WikiTree search is unavailable or returned an unsupported response. Try a website search below.')
                failed['results']=results
                if results: failed['message']='Partial results: one surname search failed. Try again later.'
                return failed
            result={'results':results,'totals':totals,'warnings':list(dict.fromkeys(warnings)),'links':website_links(q),'cached':False,'status':'ok',
                'message':'Showing up to 25 public profiles per surname; this is not an exhaustive search.'}
            if len(self.cache)>=100: self.cache.pop(next(iter(self.cache)))
            self.cache[key]=(time.monotonic()+600,copy.deepcopy(result))
            return result

    def failure(self,q,message):
        return {'results':[],'totals':[],'warnings':[],'links':website_links(q),'cached':False,'status':'unavailable','message':message}
