"""
Hook Alas to record collected stats. Imported once at the top of alas.py.

Every hook only reads values Alas has already computed, runs after the original
method returns, and never raises into Alas.
"""
import os
import sys
import threading
import time

from module.collected import hooks
from module.collected.store import CollectedStore

SHIP_IMAGE_FOLDER = './screenshots/ships'

_store = None
_store_pid = None
_store_lock = threading.Lock()
_last_instance = ''
_meow = {'coins': 0, 'count': 0}


def store():
    # One connection per process: never share a sqlite connection across fork()
    global _store, _store_pid
    with _store_lock:
        if _store is None or _store_pid != os.getpid():
            _store = CollectedStore()
            _store_pid = os.getpid()
        return _store


def instance_of(main):
    """
    Args:
        main: Any Alas module instance, or None for staticmethods.

    Returns:
        str: Config name, e.g. "alas"
    """
    global _last_instance
    name = getattr(getattr(main, 'config', None), 'config_name', None)
    if name:
        _last_instance = name
    return name or _last_instance or 'alas'


def task_of(main):
    task = getattr(getattr(main, 'config', None), 'task', None)
    return getattr(task, 'command', '') or type(main).__name__


"""
Hook handlers: (self, result, args, kwargs)
"""


def resource(key, source='', value_from=None, key_from=None):
    def handler(main, result, args, kwargs):
        value = value_from(main, result, args) if value_from else result
        k = key_from(main) if key_from else key
        store().record_resource(instance_of(main), k, value, source)

    return handler


def event_key(main):
    event = getattr(main.config, 'Campaign_Event', '') or 'unknown'
    return f'event_pt@{event}'


def on_gacha_run(main, result, args, kwargs):
    inst = instance_of(main)
    store().record_resource(inst, 'coin', getattr(main, 'build_coin_count', 0), 'gacha')
    store().record_resource(inst, 'cube', getattr(main, 'build_cube_count', 0), 'gacha')


def on_meow_get_buy_count(main, result, args, kwargs):
    # staticmethod _meow_get_buy_count(bought, total, coins, buy_amount, overflow_th)
    coins = args[2] if len(args) > 2 else kwargs.get('coins', 0)
    _meow['coins'] = coins
    store().record_resource(instance_of(main), 'meow_coin', coins, 'meowfficer')


def on_meow_choose(main, result, args, kwargs):
    _meow['count'] = args[0] if args else kwargs.get('count', 0)


def on_meow_confirm(main, result, args, kwargs):
    if _meow['count'] > 0:
        store().record_purchase(
            instance_of(main), 'Meowfficer', 'Meowfficer box', _meow['count'], 0, 'meow_coin', _meow['coins'])
    _meow['count'] = 0


def on_get_ship(main, result, args, kwargs):
    if not result:
        return
    from module.combat.assets import NEW_SHIP
    is_new = main.appear(NEW_SHIP)
    image = main.device.image
    inst = instance_of(main)
    campaign = getattr(main.config, 'Campaign_Name', '') or ''
    now = time.time()
    file = os.path.join(SHIP_IMAGE_FOLDER, f'{inst}_{int(now * 1000)}.png')
    if not store().record_ship(inst, is_new, campaign, file, now=now):
        return

    def save():
        from module.base.utils import save_image
        os.makedirs(SHIP_IMAGE_FOLDER, exist_ok=True)
        save_image(image, file)

    threading.Thread(target=save, daemon=True).start()


def on_shop_buy(main, result, args, kwargs):
    item = args[0] if args else kwargs.get('item')
    store().record_purchase(
        instance_of(main), task_of(main), str(getattr(item, 'name', item)),
        getattr(item, 'amount', 1), getattr(item, 'price', 0), str(getattr(item, 'cost', '')),
        getattr(main, '_currency', 0))


def on_os_shop_buy(main, result, args, kwargs):
    if not result:
        return
    item = args[0] if args else kwargs.get('button')
    store().record_purchase(
        instance_of(main), task_of(main), str(getattr(item, 'name', item)),
        getattr(item, 'count', 1), getattr(item, 'price', 0), str(getattr(item, 'cost', '')),
        getattr(main, '_shop_yellow_coins', 0))


def on_event_shop_buy(main, result, args, kwargs):
    item = args[0] if args else kwargs.get('item')
    amount = args[1] if len(args) > 1 else kwargs.get('amount')
    if amount is None:
        amount = getattr(item, 'count', 1)
    store().record_purchase(
        instance_of(main), 'EventShop', str(getattr(item, 'name', item)), amount,
        getattr(item, 'price', 0), str(getattr(item, 'cost', '')), getattr(main, 'pt', 0))


def action_point_value(main, result, args):
    return getattr(main, '_action_point_current', 0)


# (hook name, module, class, method, handler)
HOOKS = [
    ('oil', 'module.campaign.campaign_status', 'CampaignStatus', 'get_oil', resource('oil', 'campaign')),
    ('coin', 'module.campaign.campaign_status', 'CampaignStatus', 'get_coin', resource('coin', 'campaign')),
    ('event_pt', 'module.campaign.campaign_status', 'CampaignStatus', 'get_event_pt',
     resource('', 'event', key_from=event_key)),
    ('event_shop_oil', 'module.shop_event.ui', 'EventShopUI', 'get_oil', resource('oil', 'event_shop')),
    ('event_shop_pt', 'module.shop_event.ui', 'EventShopUI', 'event_shop_get_pt',
     resource('event_shop_pt', 'event_shop')),
    ('event_shop_urpt', 'module.shop_event.ui', 'EventShopUI', 'event_shop_get_urpt',
     resource('event_shop_urpt', 'event_shop')),
    ('shop_coin', 'module.shop.shop_status', 'ShopStatus', 'status_get_gold_coins', resource('coin', 'shop')),
    ('gem', 'module.shop.shop_status', 'ShopStatus', 'status_get_gems', resource('gem', 'shop')),
    ('medal', 'module.shop.shop_status', 'ShopStatus', 'status_get_medal', resource('medal', 'shop')),
    ('merit', 'module.shop.shop_status', 'ShopStatus', 'status_get_merit', resource('merit', 'shop')),
    ('guild_coin', 'module.shop.shop_status', 'ShopStatus', 'status_get_guild_coins',
     resource('guild_coin', 'shop')),
    ('core', 'module.shop.shop_status', 'ShopStatus', 'status_get_core', resource('core', 'shop')),
    ('voucher', 'module.shop.shop_status', 'ShopStatus', 'status_get_voucher', resource('voucher', 'shop')),
    ('gacha', 'module.gacha.gacha_reward', 'RewardGacha', 'gacha_run', on_gacha_run),
    ('meow_coin', 'module.meowfficer.buy', 'MeowfficerBuy', '_meow_get_buy_count', on_meow_get_buy_count),
    ('meow_choose', 'module.meowfficer.buy', 'MeowfficerBuy', 'meow_choose', on_meow_choose),
    ('meow_buy', 'module.meowfficer.buy', 'MeowfficerBuy', 'meow_confirm', on_meow_confirm),
    ('os_yellow_coin', 'module.os_handler.os_status', 'OSStatus', 'get_yellow_coins',
     resource('os_yellow_coin', 'opsi')),
    ('os_purple_coin', 'module.os_handler.os_status', 'OSStatus', 'get_purple_coins',
     resource('os_purple_coin', 'opsi')),
    ('os_action_point', 'module.os_handler.action_point', 'ActionPointHandler', 'action_point_update',
     resource('os_action_point', 'opsi', value_from=action_point_value)),
    ('ship', 'module.combat.combat', 'Combat', 'handle_get_ship', on_get_ship),
    ('shop_buy', 'module.shop.clerk', 'ShopClerk', 'shop_buy_execute', on_shop_buy),
    ('voucher_buy', 'module.shop.shop_voucher', 'VoucherShop', 'shop_buy_execute', on_shop_buy),
    ('os_shop_buy', 'module.os_shop.shop', 'OSShop', 'os_shop_buy_execute', on_os_shop_buy),
    ('event_shop_buy', 'module.shop_event.clerk', 'EventShopClerk', 'event_shop_buy_item_execute',
     on_event_shop_buy),
]


def _register(name, module_name, class_name, method, handler):
    def touch_then_handle(main, result, args, kwargs):
        handler(main, result, args, kwargs)
        store().touch_hook(name)

    def apply(module):
        cls = getattr(module, class_name, None)
        if cls is None:
            store().set_hook(name, 'missing', f'{module_name}.{class_name} not found')
            hooks._report(f'Collected: hook {name} missing class {module_name}.{class_name}')
            return
        if hooks.wrap_method(cls, method, touch_then_handle):
            store().set_hook(name, 'ok', f'{class_name}.{method}')
        else:
            store().set_hook(name, 'missing', f'{class_name}.{method} not found')
            hooks._report(f'Collected: hook {name} missing method {class_name}.{method}')

    if module_name not in sys.modules:
        store().set_hook(name, 'waiting', f'{module_name} not imported yet')
    hooks.when_imported(module_name, apply)


def install():
    for hook in HOOKS:
        _register(*hook)


install()
