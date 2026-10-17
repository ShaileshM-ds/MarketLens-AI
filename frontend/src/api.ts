export type Mode='demo'|'imported'|'live';
export let csrf='';
export function setCsrf(value:string){csrf=value;}
export async function api(path:string,mode:Mode,method='GET',body?:unknown){
 const response=await fetch('/api/v1'+path+(path.includes('?')?'&':'?')+'mode='+mode,{method,credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
 const result=await response.json();
 if(!response.ok)throw new Error(result.error?.message||result.detail?.[0]?.msg||'Request failed');
 return result.data;
}
export function money(value:number|null|undefined,currency='INR'){return value==null?'Not observed':new Intl.NumberFormat('en-IN',{style:'currency',currency,maximumFractionDigits:2}).format(value/(currency==='JPY'?1:100));}
export function exportJSON(name:string,data:unknown,mode:Mode){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({mode,synthetic:mode==='demo',exported_at:new Date().toISOString(),data},null,2)],{type:'application/json'}));a.download=`${mode}-${name}.json`;a.click();URL.revokeObjectURL(a.href);}
