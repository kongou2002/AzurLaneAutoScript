"""
"Collected" page in the Alas GUI. Imported once at the end of module/webui/app.py.
"""
import os
import time
from datetime import datetime, timedelta, timezone

from pywebio.output import (clear, output, put_buttons, put_collapse, put_column, put_html, put_image,
                            put_row, put_scope, put_table, put_text, use_scope)
from pywebio.session import run_js

from module.collected import hooks
from module.collected.store import CollectedStore
from module.logger import logger
from module.webui.app import AlasGUI

# Day boundaries and timestamps are shown in the user's timezone (Vietnam, UTC+7)
DISPLAY_TZ = timezone(timedelta(hours=7))
SHIP_GALLERY = 12
# A reading older than this is greyed out as possibly outdated (seconds)
STALE_AFTER = 3600

ICON = """<svg class="aside-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
 stroke-linecap="round" stroke-linejoin="round"><path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5"/>
<path d="M12 13v8"/></svg>"""

LABELS = {
    'oil': 'Oil',
    'coin': 'Coin',
    'gem': 'Gem',
    'cube': 'Wisdom Cube',
    'medal': 'Medal',
    'merit': 'Merit',
    'guild_coin': 'Guild coin',
    'core': 'Specialized core',
    'voucher': 'OpSi voucher',
    'meow_coin': 'Meowfficer coin',
    'os_yellow_coin': 'OpSi yellow coin',
    'os_purple_coin': 'OpSi purple coin',
    'os_action_point': 'OpSi action point',
    'event_shop_pt': 'Event shop PT',
    'event_shop_urpt': 'Event shop UR PT',
}


def label(key):
    if key.startswith('event_pt@'):
        return f'Event PT ({key.split("@", 1)[1]})'
    return LABELS.get(key, key)


def fmt_ts(ts):
    if not ts:
        return '-'
    return datetime.fromtimestamp(ts, DISPLAY_TZ).strftime('%m-%d %H:%M')


def fmt_delta(n):
    if not n:
        return '0'
    return f'{n:+,}'


def fmt_flow(row, period):
    """
    Income and spending above the net change, e.g. "+17,347 / -6,000" then "net +11,347"
    """
    income, spent, net = row[f'income_{period}'], row[f'spent_{period}'], row[f'delta_{period}']
    return put_html(
        f'<span style="color:#28a745">+{income:,}</span> / <span style="color:#dc3545">-{spent:,}</span>'
        f'<br><small style="color:#6c757d">net {fmt_delta(net)}</small>')


def fmt_current(row, now=None):
    now = time.time() if now is None else now
    text = f"{row['latest']:,}"
    age = now - row['latest_ts']
    if age > STALE_AFTER:
        return put_html(f'<span style="color:#adb5bd" title="Read {int(age // 60)} minutes ago, may be outdated">'
                        f'{text}</span><br><small style="color:#adb5bd">{int(age // 3600)}h ago</small>')
    return text


def day_start(days_ago=0):
    now = datetime.now(DISPLAY_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return start.timestamp()


def thumbnail(path, width=320):
    # The GUI process replaces PIL with module/webui/fake_pil_module.py, use cv2 instead
    try:
        import cv2
        image = cv2.imread(path)
        if image is None:
            return None
        h, w = image.shape[:2]
        if w > width:
            image = cv2.resize(image, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None
    except Exception as e:
        logger.warning(f'Collected: cannot read ship image {path}: {e!r}')
        return None


class CollectedPage:
    def __init__(self):
        self.store = CollectedStore()
        self.instance = None
        self.chart_key = 'oil'

    def render(self):
        instances = self.store.instances()
        if self.instance not in instances:
            self.instance = instances[0] if instances else None
        clear('content')
        with use_scope('content'):
            put_scope('collected_head')
            put_scope('collected_resource')
            put_scope('collected_chart')
            put_scope('collected_ship')
            put_scope('collected_purchase')
            put_scope('collected_hook')
        self.render_head(instances)
        if self.instance is None:
            put_text('No data yet. Stats are recorded while Alas runs tasks.', scope='collected_head')
        else:
            self.render_resource()
            self.render_chart()
            self.render_ship()
            self.render_purchase()
        self.render_hook()

    def render_head(self, instances):
        with use_scope('collected_head', clear=True):
            buttons = [{'label': 'Refresh', 'value': '__refresh__', 'color': 'info'}]
            buttons += [{'label': i, 'value': i, 'color': 'primary' if i == self.instance else 'secondary'}
                        for i in instances]
            put_buttons(buttons, onclick=self.on_head)

    def on_head(self, value):
        if value != '__refresh__':
            self.instance = value
        self.render()

    def render_resource(self):
        rows = self.store.resource_summary(self.instance, day_start=day_start(), week_start=day_start(7))
        with use_scope('collected_resource', clear=True):
            put_html('<h4>Resources</h4>')
            if not rows:
                put_text('No resource readings yet.')
                return
            first = min(r['first_ts'] for r in rows)
            put_text(f'Tracking since {fmt_ts(first)} (UTC+7). Each period shows income / spending between '
                     f'readings, then the net change. Income and spending between two readings cancel out, '
                     f'so both are minimums. Grey values are over an hour old.')
            put_table(
                [[label(r['key']), fmt_current(r), fmt_flow(r, 'today'), fmt_flow(r, 'week'),
                  fmt_flow(r, 'all'), fmt_ts(r['latest_ts'])] for r in rows],
                header=['Resource', 'Current', 'Today', '7 days', 'Since start', 'Last read'])

    def render_chart(self):
        keys = [r['key'] for r in self.store.resource_summary(self.instance, day_start(), day_start(7))]
        if not keys:
            return
        if self.chart_key not in keys:
            self.chart_key = keys[0]
        daily = self.store.resource_daily(self.instance, self.chart_key, tz_offset_hours=7)
        with use_scope('collected_chart', clear=True):
            put_html('<h4>Daily value</h4>')
            put_buttons(
                [{'label': label(k), 'value': k, 'color': 'primary' if k == self.chart_key else 'secondary'}
                 for k in keys],
                onclick=self.on_chart, small=True)
            put_html('<div id="collected-chart" style="width:100%;height:320px"></div>')
        run_js(
            """
            require(['plotly'], function (Plotly) {
                Plotly.newPlot('collected-chart',
                    [{x: days, y: values, type: 'scatter', mode: 'lines+markers', name: name}],
                    {margin: {t: 20, r: 20, b: 40, l: 60}, yaxis: {tickformat: ','}},
                    {displayModeBar: false, responsive: true});
            });
            """,
            days=[d for d, _ in daily], values=[v for _, v in daily], name=label(self.chart_key))

    def on_chart(self, key):
        self.chart_key = key
        self.render_chart()

    def render_ship(self):
        today, today_new = self.store.ship_count(self.instance, since=day_start())
        week, week_new = self.store.ship_count(self.instance, since=day_start(7))
        total, total_new = self.store.ship_count(self.instance)
        with use_scope('collected_ship', clear=True):
            put_html('<h4>Ships</h4>')
            put_table([[f'{today} ({today_new} new)', f'{week} ({week_new} new)', f'{total} ({total_new} new)']],
                      header=['Today', '7 days', 'Since start'])
            cards = []
            for ship in self.store.ships(self.instance, limit=SHIP_GALLERY):
                caption = f"{fmt_ts(ship['ts'])} {ship['campaign']}" + (' NEW' if ship['is_new'] else '')
                data = thumbnail(ship['image']) if os.path.exists(ship['image']) else None
                cards.append(put_column([
                    put_image(data, width='240px') if data else put_text('(image missing)'),
                    put_text(caption),
                ], size='auto'))
            if cards:
                put_html('<div style="height:.5rem"></div>')
                put_row(cards[:4], size=' '.join(['1fr'] * len(cards[:4])))
                for i in range(4, len(cards), 4):
                    chunk = cards[i:i + 4]
                    put_row(chunk, size=' '.join(['1fr'] * len(chunk)))

    def render_purchase(self):
        totals = self.store.purchase_totals(self.instance)
        recent = self.store.purchases(self.instance, limit=30)
        with use_scope('collected_purchase', clear=True):
            put_html('<h4>Purchases</h4>')
            if not totals:
                put_text('No purchases recorded yet.')
                return
            put_table(
                [[t['shop'], t['item'], f"{t['amount']:,}", t['times'], t['currency'], fmt_ts(t['last_ts'])]
                 for t in totals],
                header=['Shop', 'Item', 'Amount', 'Times', 'Currency', 'Last'])
            put_collapse('Recent purchases', [put_table(
                [[fmt_ts(p['ts']), p['shop'], p['item'], p['amount'], f"{p['price']:,}", p['currency'],
                  f"{p['balance']:,}"] for p in recent],
                header=['Time', 'Shop', 'Item', 'Amount', 'Price', 'Currency', 'Balance before'])])

    def render_hook(self):
        rows = self.store.hooks()
        broken = [h for h in rows if h['status'] == 'missing']
        with use_scope('collected_hook', clear=True):
            if broken:
                put_html('<div style="color:#dc3545">'
                         f'{len(broken)} hook(s) not working after an Alas update: '
                         f'{", ".join(h["name"] for h in broken)}</div>')
            put_collapse('Recorder hooks', [put_table(
                [[h['name'], h['status'], h['detail'], fmt_ts(h['last_fired'])] for h in rows],
                header=['Hook', 'Status', 'Target', 'Last fired'])], open=bool(broken))


def ui_collected(gui):
    gui.init_aside(expand_menu=False, name='Collected')
    gui.collapse_menu()
    gui.set_title('Collected')
    gui.alas_name = ''
    if hasattr(gui, 'alas'):
        del gui.alas
    gui.state_switch.switch()
    if not hasattr(gui, '_collected_page'):
        gui._collected_page = CollectedPage()
    gui._collected_page.render()


def put_aside_button(gui, result, args, kwargs):
    # Insert before the "Manage" button, which set_aside() puts last
    put_column([
        output(put_html(ICON)).style('z-index: 1; margin-left: 8px;text-align: center'),
        put_buttons([{'label': 'Collected', 'value': 'Collected', 'color': 'aside'}],
                    onclick=[lambda: ui_collected(gui)]).style('z-index: 2; --aside-Collected--;'),
    ], size='0', scope='aside', position=-2)


if hooks.wrap_method(AlasGUI, 'set_aside', put_aside_button):
    logger.info('Collected: GUI page installed')
else:
    logger.warning('Collected: AlasGUI.set_aside not found, GUI page not installed')
