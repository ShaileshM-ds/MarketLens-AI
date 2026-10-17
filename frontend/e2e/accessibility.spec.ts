import {test,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
test('overview accessibility and product chart alternative',async({page})=>{
 await page.goto('/');await expect(page.getByText('36',{exact:true})).toBeVisible();
 const result=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
 fs.writeFileSync('../submission/accessibility-results.json',JSON.stringify({url:page.url(),violations:result.violations,incomplete:result.incomplete.map(x=>({id:x.id,description:x.description}))},null,2));
 expect(result.violations.filter(v=>v.impact==='critical'||v.impact==='serious')).toEqual([]);
});
