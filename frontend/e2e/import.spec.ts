import {test,expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
test('administrator import preview, commit and repeat-import deduplication',async({page})=>{
 test.skip(!!process.env.TEST_BASE_URL,'Local fixture uses dedicated development MongoDB; API integration also covers this flow.');
 const root=path.resolve('..');const id=randomUUID().replaceAll('-','');const username='browser-'+id;const password=randomUUID()+'-Test';const external_id='BROWSER-'+id;
 const fixture=(action:string)=>execFileSync('python',[path.join(root,'scripts/browser_admin_fixture.py')],{cwd:root,input:JSON.stringify({action,id,username,password,external_id}),env:{...process.env,PYTHONPATH:[path.join(root,'.runtime'),path.join(root,'backend')].join(path.delimiter),MONGO_URI:'mongodb://127.0.0.1:27028'}});
 fixture('create');
 try{
  await page.goto('/');await page.getByLabel('Data mode').selectOption('imported');
  await page.getByRole('button',{name:'Guest workspace'}).click();
  await page.getByLabel('Username',{exact:true}).fill(username);await page.getByLabel('Password',{exact:true}).fill(password);await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.getByRole('button',{name:'Data administration',exact:true}).click();
  await page.getByLabel('Or paste content').fill(JSON.stringify([{asin:external_id,title:'Browser import evidence',marketplace:'amazon.in',currency:'INR',price:'100.25',source_id:external_id}]));
  await page.getByRole('button',{name:'Profile and preview',exact:true}).click();
  await expect(page.getByRole('button',{name:'Import into isolated database'})).toBeVisible();
  await page.getByRole('button',{name:'Import into isolated database'}).click();await expect(page.getByRole('status')).toContainText('"accepted":1');
  await page.getByRole('button',{name:'Import into isolated database'}).click();await expect(page.getByRole('status')).toContainText('"duplicates":1');
  await page.screenshot({path:'../submission/screenshots/import-admin.png',fullPage:true});
 }finally{fixture('delete');}
});
