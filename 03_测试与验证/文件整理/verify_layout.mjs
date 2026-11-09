// Read-only page smoke check after the approved layout change.
import { chromium } from '../系统测试/runs/20261105_chart_refresh/node_modules/playwright-core/index.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'../../电商运营系统');
const ini=await fs.readFile(path.join(root,'config/local/登录配置.ini'),'utf8');
const section=ini.match(/\[admin\]([\s\S]*?)(?:\n\[|$)/)[1];
const account=Object.fromEntries(section.trim().split(/\r?\n/).filter(x=>x.includes('=')).map(x=>{
  const i=x.indexOf('=');return [x.slice(0,i).trim(),x.slice(i+1).trim()];
}));
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
const response=await context.request.post('http://127.0.0.1:8777/api/auth/login',{data:account});
if(response.status()!==200)throw new Error('Login failed');
const page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(String(e)));
try {
  await page.goto('http://127.0.0.1:8777/?release=layout-20261105#/forecast',{waitUntil:'networkidle'});
  await page.waitForFunction(()=>{
    const c=document.querySelector('.forecast-results .ece-chart canvas');
    if(!c?.width)return false;
    const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
    let green=0;
    for(let i=0;i<p.length;i+=4)if(p[i+3]>100&&p[i]<80&&p[i+1]>80&&p[i+1]<170&&p[i+2]>60&&p[i+2]<150)green++;
    return green>150;
  });
  await page.locator('.ece-chart').screenshot({path:path.join(here,'20261105_预测页面.png')});
  await page.goto('http://127.0.0.1:8777/?release=layout-20261105#/inventory',{waitUntil:'networkidle'});
  await page.getByRole('heading',{name:'商品库存概览',exact:true}).waitFor();
  const text=await page.locator('.ece-page').innerText();
  if(!text.includes('DISCO BALL CHRISTMAS DECORATION')||!text.includes('1,540'))throw new Error('Existing stock not shown');
  if(errors.length)throw new Error(errors.join('\n'));
  await fs.writeFile(path.join(here,'20261105_browser_checks.json'),JSON.stringify({savedForecastPainted:true,existingInventoryVisible:true,pageErrors:errors},null,2));
  console.log('PASS: saved forecast curve and existing inventory render from the reorganized application.');
} finally {
  await context.request.post('http://127.0.0.1:8777/api/auth/logout',{data:{}});
  await browser.close();
}
