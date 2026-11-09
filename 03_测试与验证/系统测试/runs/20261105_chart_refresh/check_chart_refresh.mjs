// Test the real app in a separate headless Edge profile, never the user's browser.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../../../电商运营系统');
const ini = await fs.readFile(path.join(root, 'config/local/登录配置.ini'), 'utf8');
const adminSection = ini.match(/\[admin\]([\s\S]*?)(?:\n\[|$)/)[1];
const admin = Object.fromEntries(adminSection.trim().split(/\r?\n/).filter(x => x.includes('=')).map(line => {
  const i = line.indexOf('='); return [line.slice(0, i).trim(), line.slice(i + 1).trim()];
}));
const base = 'http://127.0.0.1:8777';
const checks = [], errors = [];
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' });
const login = await context.request.post(base + '/api/auth/login', { data: admin });
if (login.status() !== 200) throw new Error('Local account login failed: HTTP ' + login.status());
const page = await context.newPage();
page.on('pageerror', error => errors.push(String(error)));

async function painted(label) {
  await page.waitForFunction(() => {
    const el = document.querySelector('.forecast-results .ece-chart');
    const c = el?.querySelector('canvas');
    if (!c || !c.width || !c.height || !el.clientWidth) return false;
    const px = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let green = 0;
    for (let i = 0; i < px.length; i += 4)
      if (px[i + 3] > 100 && px[i] < 80 && px[i + 1] > 80 && px[i + 1] < 170 && px[i + 2] > 60 && px[i + 2] < 150) green++;
    return green > 150;
  }, undefined, { timeout: 30000 });
  checks.push({ case: label, ...await page.locator('.forecast-results .ece-chart').evaluate(el => ({
    instance: el.getAttribute('_echarts_instance_'), width: el.clientWidth, height: el.clientHeight,
    canvasCount: el.querySelectorAll('canvas').length,
    pixelWidth: el.querySelector('canvas').width, pixelRatio: window.devicePixelRatio,
  })) });
}
async function sized() {
  await page.waitForFunction(() => {
    const el = document.querySelector('.forecast-results .ece-chart'), c = el?.querySelector('canvas');
    return c && Math.abs(c.width / window.devicePixelRatio - el.clientWidth) < 3;
  });
}

try {
  await page.goto(base + '/#/forecast', { waitUntil: 'networkidle' });
  await painted('restore_saved_result');
  for (const origin of ['2011-07-05', '2011-07-06']) {
    const old = await page.locator('.forecast-results .ece-chart').elementHandle();
    const field = page.getByRole('combobox', { name: '预测起点', exact: true });
    await field.fill(origin); await field.press('Tab');
    const pending = page.waitForResponse(r => r.url().endsWith('/api/forecast') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '生成未来 7 天预测', exact: true }).click();
    if ((await pending).status() !== 202) throw new Error('Forecast submission failed');
    if (await old.evaluate(el => el.isConnected)) throw new Error('Expected old container removal');
    await painted('generate_' + origin);
    await page.locator('.forecast-results').getByText('起点 ' + origin, { exact: false }).first().waitFor();
  }
  await page.locator('.forecast-results .ece-chart').screenshot({ path: path.join(here, '预测曲线_连续生成后.png') });
  await page.locator('.hamburger-container').click();
  await sized(); await painted('sidebar_layout_change');
  await page.setViewportSize({ width: 1000, height: 850 });
  await sized(); await painted('responsive_resize');
  await page.reload({ waitUntil: 'networkidle' }); await painted('reload_after_new_prediction');
  await page.goto(base + '/#/orders', { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: '商品与订单', exact: true }).waitFor();
  await page.locator('.forecast-results .ece-chart').waitFor({ state: 'detached' });
  await page.goto(base + '/#/forecast', { waitUntil: 'networkidle' }); await painted('leave_and_return');
  if (errors.length) throw new Error(errors.join('\n'));
  await fs.writeFile(path.join(here, 'browser_results.json'), JSON.stringify({ checks, pageErrors: errors }, null, 2));
  console.log(`PASS: ${checks.length} real-page chart checks; 0 page errors; credentials omitted.`);
} catch (error) {
  await page.screenshot({ path: path.join(here, 'failure.png'), fullPage: true });
  await fs.writeFile(path.join(here, 'failure.txt'), String(error));
  throw error;
} finally {
  await context.request.post(base + '/api/auth/logout', { data: {} });
  await browser.close();
}
