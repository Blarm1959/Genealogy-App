"""Collapse identical source links, never infer that people share an identity."""
import copy
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

def canonical(url):
    p=urlsplit(url.strip())
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('Use a complete public http/https source URL without credentials.')
    query=urlencode(sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')))
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path or '/',query,''))

def add_lead(payload,lead):
    data=copy.deepcopy(payload);items=data.setdefault('results',[]);key=canonical(lead['url'])
    for item in items:
        if canonical(item['url'])==key:
            observations=item.setdefault('observations',[])
            observation={'source':lead['source'],'url':lead['url'],'notes':lead['notes']}
            if observation not in observations:observations.append(observation)
            item['duplicate_count']=item.get('duplicate_count',1)+1
            return data
    items.append({'id':key,'name':lead['name'],'source':lead['source'],'url':lead['url'],'kind':'Manually recorded research lead','birth':'','death':'','birthplace':'','deathplace':'','reasons':[],'conflicts':[],'unknown':['User-supplied details; not independently verified.'],'notes':lead['notes'],'duplicate_count':1})
    return data
