export function apiFetch(input:string,init:RequestInit={}){
 const headers=new Headers(init.headers);const token=document.cookie.split('; ').find(v=>v.startsWith('smartseat_csrf='))?.split('=')[1];if(token)headers.set('X-CSRF-Token',decodeURIComponent(token));return fetch(input,{...init,credentials:'include',headers});
}
