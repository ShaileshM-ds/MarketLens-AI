// @vitest-environment jsdom
import {render,screen} from '@testing-library/react';
import {it,expect,vi} from 'vitest';
import Chart from './Chart';
vi.mock('echarts',()=>({init:()=>({setOption:vi.fn(),resize:vi.fn(),dispose:vi.fn()})}));
it('provides exact observed data as an accessible alternative to canvas',()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){} disconnect(){}});
 vi.stubGlobal('matchMedia',()=>({matches:true}));
 render(<Chart dates={['2026-01-01','2026-01-02']} series={[{name:'Observed',values:[12345,null]}]}/>);
 expect(screen.getByRole('img').getAttribute('aria-label')).toContain('data table');
 expect(screen.getByText('12345')).toBeTruthy();expect(screen.getByText('Not observed')).toBeTruthy();
});
