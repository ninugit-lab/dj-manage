// End-to-End-Smoketest der gesamten DJ-Wishlist-App via Playwright.
// Deckt Login, Dashboard, Preiskalkulator (Posten/Paket/Regel/Formel +
// Workflow inkl. Regelauswahl), Event-Anlage, oeffentliche Wishlist,
// Buchungsformular, Config und Kalender ab.
//
// Ausfuehrung: siehe tests/e2e/README.md
// Konfiguration via ENV: TARGET_URL, E2E_USER, E2E_PASS

const { chromium } = require('playwright');

const BASE = process.env.TARGET_URL || 'http://localhost:8500';
const USER = process.env.E2E_USER || 'pwtest';
const PASS = process.env.E2E_PASS || 'PwTest!2026';
const HEADLESS = process.env.E2E_HEADLESS !== 'false';
const SHOT = process.env.E2E_SHOT_DIR || '/tmp/dj-e2e';

let pass = 0, fail = 0;
const fails = [];
function ok(name) { pass++; console.log('  ✅ ' + name); }
function bad(name, err) { fail++; fails.push(name + ' -> ' + err); console.log('  ❌ ' + name + ' :: ' + err); }
async function step(name, fn) {
  try { await fn(); ok(name); } catch (e) { bad(name, e.message.split('\n')[0]); }
}

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, slowMo: 30 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') pageErrors.push('console: ' + m.text()); });

  const ts = Date.now();
  const eventName = 'E2E Hochzeit ' + ts;
  const itemName = 'E2E Posten ' + ts;
  const pkgName = 'E2E Paket ' + ts;
  const ruleName = 'E2E Regel ' + ts;
  const formulaName = 'E2E Formel ' + ts;
  const wfName = 'E2E Workflow ' + ts;

  console.log('\n=== 1. LOGIN ===');
  await step('Login-Seite laden', async () => {
    await page.goto(BASE + '/admin/login/', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('input[name="username"]', { timeout: 10000 });
  });
  await step('Anmelden als Superuser', async () => {
    await page.fill('input[name="username"]', USER);
    await page.fill('input[name="password"]', PASS);
    await page.click('input[type="submit"], button[type="submit"]');
    await page.waitForLoadState('networkidle', { timeout: 15000 });
  });

  console.log('\n=== 2. DASHBOARD ===');
  await step('Dashboard erreichbar', async () => {
    await page.goto(BASE + '/dj-admin/', { waitUntil: 'networkidle', timeout: 20000 });
    if (/login/.test(page.url())) throw new Error('Redirect zu Login - Auth fehlgeschlagen');
    await page.screenshot({ path: SHOT + '-dashboard.png', fullPage: true });
  });

  console.log('\n=== 3. PREISKALKULATOR / WORKFLOW-BUILDER ===');
  await step('Workflow-Builder laden', async () => {
    await page.goto(BASE + '/dj-admin/workflow/', { waitUntil: 'networkidle', timeout: 20000 });
    await page.waitForSelector('#wf-canvas', { timeout: 10000 });
    await page.screenshot({ path: SHOT + '-workflow.png', fullPage: true });
  });

  await step('Preis-Posten anlegen', async () => {
    await page.click('button:has-text("Neuer Posten")');
    await page.waitForSelector('#item-name', { state: 'visible', timeout: 5000 });
    await page.fill('#item-name', itemName);
    await page.fill('#item-price', '150');
    await page.locator('#item-add button:has-text("Speichern")').click();
    await page.waitForTimeout(800);
  });

  await step('Paket anlegen', async () => {
    await page.click('.wb-comp-tab:has-text("Pakete")');
    await page.click('button:has-text("Neues Paket")');
    await page.waitForSelector('#pkg-name', { state: 'visible', timeout: 5000 });
    await page.fill('#pkg-name', pkgName);
    await page.fill('#pkg-price', '1000');
    await page.locator('#pkg-add button:has-text("Speichern")').click();
    await page.waitForTimeout(800);
  });

  await step('Regel anlegen', async () => {
    await page.click('.wb-comp-tab:has-text("Regeln")');
    await page.click('button:has-text("Neue Regel")');
    await page.waitForSelector('#rule-name', { state: 'visible', timeout: 5000 });
    await page.fill('#rule-name', ruleName);
    const effVal = page.locator('#rule-effect-val, #rule-amount').first();
    if (await effVal.count()) await effVal.fill('100');
    await page.locator('#rule-add button:has-text("Speichern")').click();
    await page.waitForTimeout(800);
  });

  await step('Formel anlegen', async () => {
    await page.click('.wb-comp-tab:has-text("Formeln")');
    await page.click('button:has-text("Neue Formel")');
    await page.waitForSelector('#formula-expr', { state: 'visible', timeout: 5000 });
    await page.fill('#formula-name', formulaName);
    await page.fill('#formula-expr', 'base + guests * 5');
    await page.locator('#formula-add button:has-text("Speichern")').click();
    await page.waitForTimeout(800);
  });

  await step('Workflow-Bloecke per Klick hinzufuegen (Paket + Regeln)', async () => {
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('.wb-block[data-type="package"]', { timeout: 10000 });
    // Warten bis wfContext geladen ist (sonst keine Regelauswahl-UI)
    await page.waitForFunction(() => window.wfContext && window.wfContext.rules, null, { timeout: 10000 }).catch(() => {});
    await page.click('.wb-block[data-type="package"]');
    await page.click('.wb-block[data-type="rules"]');
    await page.waitForTimeout(500);
    const steps = await page.locator('.wb-step').count();
    if (steps < 2) throw new Error('Erwartet >=2 Bloecke, gefunden ' + steps);
  });

  await step('Regelauswahl-UI im Regeln-Block vorhanden', async () => {
    const rulesStep = page.locator('.wb-step').nth(1);
    await rulesStep.waitFor({ timeout: 5000 });
    await page.waitForFunction(() => {
      const steps = document.querySelectorAll('.wb-step');
      return steps[1] && steps[1].querySelectorAll('input[type="checkbox"]').length > 0;
    }, null, { timeout: 8000 });
    const n = await rulesStep.locator('input[type="checkbox"]').count();
    if (n < 1) throw new Error('Keine Regel-Checkboxen im Regeln-Block gefunden');
    await page.screenshot({ path: SHOT + '-rules-select.png', fullPage: true });
  });

  await step('Einzelne Regel auswaehlen', async () => {
    const rulesStep = page.locator('.wb-step').nth(1);
    const cbs = rulesStep.locator('input[type="checkbox"]');
    // "Alle aktiven Regeln" abwaehlen -> Einzelregeln werden aktivierbar
    await cbs.first().uncheck().catch(() => {});
    await page.waitForTimeout(300);
    const single = rulesStep.locator('input[type="checkbox"]').nth(1);
    if (await single.count()) await single.check().catch(() => {});
    await page.waitForTimeout(300);
  });

  await step('Workflow speichern', async () => {
    await page.fill('#wf-name', wfName);
    await page.click('button:has-text("Speichern")');
    await page.waitForTimeout(1200);
  });

  await step('Workflow testen (Berechnung)', async () => {
    const testBtn = page.locator('button:has-text("Testen")').first();
    if (await testBtn.count()) {
      await testBtn.click();
      await page.waitForTimeout(1200);
      const res = await page.locator('#test-result').innerText().catch(() => '');
      if (!res || !res.trim()) throw new Error('Kein Testergebnis angezeigt');
    } else { throw new Error('Test-Button nicht gefunden'); }
  });

  console.log('\n=== 4. EVENT ANLEGEN ===');
  await step('Event-Formular laden', async () => {
    await page.goto(BASE + '/dj-admin/events/new/', { waitUntil: 'networkidle', timeout: 20000 });
    await page.waitForSelector('input[name="name"]', { timeout: 10000 });
  });
  await step('Event-Felder ausfuellen + speichern', async () => {
    await page.fill('input[name="name"]', eventName);
    await page.fill('input[name="date"]', '2026-09-15');
    await page.fill('input[name="location"]', 'Schloss Testberg');
    const guests = page.locator('input[name="guest_count"]');
    if (await guests.count()) await guests.fill('120');
    const tStart = page.locator('input[name="time_start"]');
    if (await tStart.count()) await tStart.fill('18:00');
    const cname = page.locator('input[name="client_name"]');
    if (await cname.count()) await cname.fill('Max Mustermann');
    const cmail = page.locator('input[name="client_email"]');
    if (await cmail.count()) await cmail.fill('max@example.com');
    await page.screenshot({ path: SHOT + '-event-form.png', fullPage: true });
    await page.click('button[type="submit"]:has-text("erstellen"), button[type="submit"]:has-text("speichern"), button[type="submit"]');
    await page.waitForLoadState('networkidle', { timeout: 15000 });
  });
  await step('Event erscheint im Dashboard', async () => {
    await page.goto(BASE + '/dj-admin/', { waitUntil: 'networkidle', timeout: 20000 });
    const found = await page.locator('body', { hasText: eventName }).count();
    if (!found) throw new Error('Angelegtes Event nicht im Dashboard sichtbar');
  });

  console.log('\n=== 5. OEFFENTLICHE WISHLIST ===');
  await step('Wishlist-Startseite laedt', async () => {
    await page.goto(BASE + '/', { waitUntil: 'networkidle', timeout: 20000 });
    await page.screenshot({ path: SHOT + '-public-wishlist.png', fullPage: true });
  });
  await step('Such-Input auf Wishlist (falls aktives Event)', async () => {
    const inp = page.locator('#search-input');
    if (await inp.count()) {
      await inp.fill('test');
      await page.waitForTimeout(1500); // Spotify-Suche
    } else {
      console.log('     (kein aktives Event / Suchfeld - uebersprungen)');
    }
  });

  console.log('\n=== 6. OEFFENTLICHES BUCHUNGSFORMULAR ===');
  await step('Buchungsformular laden', async () => {
    await page.goto(BASE + '/buchen/', { waitUntil: 'networkidle', timeout: 20000 });
    await page.screenshot({ path: SHOT + '-buchen.png', fullPage: true });
    if (await page.locator('#f-date').count() === 0 && await page.locator('input[type="date"]').count() === 0)
      throw new Error('Kein Datumsfeld im Buchungsformular');
  });

  console.log('\n=== 7. CONFIG-SEITE ===');
  await step('Config-Seite laedt', async () => {
    await page.goto(BASE + '/dj-admin/config/', { waitUntil: 'networkidle', timeout: 20000 });
    await page.screenshot({ path: SHOT + '-config.png', fullPage: true });
  });

  console.log('\n=== 8. KALENDER ===');
  await step('Kalender-Seite laedt', async () => {
    await page.goto(BASE + '/dj-admin/calendar/', { waitUntil: 'networkidle', timeout: 20000 });
  });

  console.log('\n========== ERGEBNIS ==========');
  console.log('PASS: ' + pass + '   FAIL: ' + fail);
  if (fails.length) { console.log('\nFehlgeschlagen:'); fails.forEach(f => console.log('  - ' + f)); }
  if (pageErrors.length) {
    console.log('\nJS/Console-Fehler im Browser:');
    [...new Set(pageErrors)].slice(0, 15).forEach(e => console.log('  ! ' + e));
  }
  console.log('\nScreenshots: ' + SHOT + '-*.png');
  await browser.close();
  process.exit(fail > 0 ? 1 : 0);
})();
