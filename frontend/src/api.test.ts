import {describe,it,expect,vi,afterEach} from 'vitest';
import {api,money,setCsrf} from './api';
describe('client contracts',()=>{
 afterEach(()=>vi.unstubAllGlobals());
 it('preserves unknown money and minor-unit scales',()=>{expect(money(null)).toBe('Not observed');expect(money(12345,'USD')).toBe('$123.45');expect(money(100,'JPY')).toContain('100');});
 it('sends mode and CSRF and surfaces server errors',async()=>{const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({data:{saved:true}})});vi.stubGlobal('fetch',fetcher);setCsrf('token');expect(await api('/watchlists','demo','PUT',{product_id:'a'})).toEqual({saved:true});expect(fetcher.mock.calls[0][0]).toContain('mode=demo');expect(fetcher.mock.calls[0][1].headers['X-CSRF-Token']).toBe('token');fetcher.mockResolvedValue({ok:false,json:async()=>({error:{message:'Denied'}})});await expect(api('/admin','demo')).rejects.toThrow('Denied');});
});
