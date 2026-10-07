'use strict';
// Static search launcher. No API proxy, cookies, credentials or persistence.
const $=id=>document.getElementById(id);
fetch('release.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json()}).then(d=>{$('version').textContent=d.version?`v${d.version}`:'Development';}).catch(()=>{$('version').textContent='Development';});
let currentDetails='';
function url(base,params){const u=new URL(base);for(const[k,v]of Object.entries(params))if(v!==''&&v!==null&&v!==undefined)u.searchParams.set(k,String(v));return u.href;}
function wiki(q,surname){return url('https://www.wikitree.com/index.php',{title:'Special:SearchPerson',wpSearch:1,wpFirst:q.first,wpLast:surname});}
function family(q,surname){return url('https://www.familysearch.org/search/record/results',{'q.givenName':q.first,'q.surname':surname});}
function createCard(name,note,links){const card=document.createElement('article');card.className='site';const title=document.createElement('strong');title.textContent=name;const text=document.createElement('span');text.textContent=note;const group=document.createElement('div');group.className='site-links';for(const[label,href]of links){const a=document.createElement('a');a.textContent=label+' ↗';a.href=href;a.target='_blank';a.rel='noopener noreferrer';group.append(a);}card.append(title,text,group);return card;}
$('search-form').addEventListener('submit',e=>{e.preventDefault();const q=Object.fromEntries([...new FormData(e.currentTarget)].map(([k,v])=>[k,String(v).trim()]));$('error').hidden=true;if(!q.last){$('error').textContent='Enter a surname.';$('error').hidden=false;return;}const variants=q.variants.split(',').map(s=>s.trim()).filter(Boolean);if(variants.length>2){$('error').textContent='Use up to two additional surname spellings.';$('error').hidden=false;return;}const surnames=[...new Set([q.last,...variants])];const name=[q.first,q.last].filter(Boolean).join(' ');$('search-title').textContent='Searches for '+name;
const names=surnames.map(s=>[s,wiki(q,s)]);
const providers=[
['WikiTree','Contributed family-tree profiles. Names are prefilled. Add dates, birthplace and parents on the site using your search details below; confirm claims against cited records.',names],
['FamilySearch','Free historical-record search; a free account may be required. Names prefilled. Add your dates, places and relatives on the site. Some images have access restrictions.',surnames.map(s=>[s,family(q,s)])],
['FreeBMD','Free England and Wales birth, marriage and death indexes. Enter the search details on the provider’s form.',[['Open search','https://www.freebmd.org.uk/search']]],
['FreeREG','Free parish-register transcriptions. Enter the search details on the provider’s form.',[['Open search','https://www.freereg.org.uk/']]],
['FreeCEN','Free census transcriptions. Enter the search details on the provider’s form.',[['Open search','https://www.freecen.org.uk/']]],
['FIBIS','Free names and transcriptions for British India research, useful for India and Burma connections. Enter the search details on the provider’s form.',[['Open database','https://search.fibis.org/bin/index.php']]],
['The National Archives','Free archive catalogue descriptions; copies or downloads may cost. Names prefilled. Add dates and places on the site.',surnames.map(s=>[s,url('https://discovery.nationalarchives.gov.uk/results/r',{'_q':[q.first,s].filter(Boolean).join(' ')})])]
];$('sources').replaceChildren(...providers.map(p=>createCard(...p)));
currentDetails=`Name: ${name}\nOther surnames: ${variants.join(', ')||'None entered'}\nBirth year: ${q.birth||'Unknown'} (±${q.spread} years)\nDeath year: ${q.death||'Unknown'} (±${q.spread} years)\nBirthplace: ${q.place||'Unknown'}\nFather: ${[q.fatherFirst,q.fatherLast].filter(Boolean).join(' ')||'Unknown'}\nMother: ${[q.motherFirst,q.motherLast].filter(Boolean).join(' ')||'Unknown'}\n\nCheck each provider’s filters before searching. Historical place names and boundaries may differ. Check source citations before accepting a match.`;$('details-text').value=currentDetails;$('copy-status').textContent='';$('searches').hidden=false;});
$('search-form').addEventListener('reset',()=>{$('searches').hidden=true;$('error').hidden=true;$('sources').replaceChildren();$('details-text').value='';currentDetails='';});
$('details-text').addEventListener('focus',e=>e.target.select());
$('copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(currentDetails);$('copy-status').textContent='Copied.';}catch{$('details-text').focus();$('details-text').select();$('copy-status').textContent='Press Ctrl+C, or use your phone’s Copy option.';}});
$('download').addEventListener('click',()=>{const href=URL.createObjectURL(new Blob([currentDetails],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=href;a.download='genealogy-search-details.txt';a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);});
