import requests
import base64
import re
import sys
import traceback
import config
from datetime import date, timedelta, datetime
from slackclient import SlackClient
from argparse import ArgumentParser


class Merchant:
    def __init__(self, uri, merchant_id, icon):
        self.uri = uri
        self.id = merchant_id
        self.ic = icon


class Env:
    MERCHANTS = {
        'bento': Merchant('https://www.now.vn/ho-chi-minh/com-trua-bento-nguyen-huu-tho-now-station-2/', 22220,
                          ':bento:'),
        'test_phuclong': Merchant('https://www.now.vn/ho-chi-minh/phuc-long-coffee-tea-house-van-hanh-mall/', 31373,
                                  ':starbucks:'),
    }
    merchant = None
    teamx_id = None

    def __init__(self):
        pass


def set_up_env(env):
    teamx_id = {
        'test': 'CBUB3P4LU',
        'prd': 'GAJ042SJ0',  # vnrnd-teamx
    }

    merchant = {
        'test': Env.MERCHANTS['test_phuclong'],
        'prd': Env.MERCHANTS['bento'],
    }
    Env.teamx_id = teamx_id[env]
    Env.merchant = merchant[env]


class Order:
    def __init__(self, merchant_id, merchant_uri, share_code, share_cart_id=0):
        self.merchant_id = merchant_id
        self.share_code = share_code
        self.merchant_uri = merchant_uri
        self.share_cart_id = share_cart_id

    def url(self):
        return "%s?t=%s" % (self.merchant_uri, self.share_code)


class CartItem:
    def __init__(self, dish_name, owner_name, quantity, is_available=True, is_done=True):
        self.dish_name = dish_name
        self.owner_name = owner_name
        self.quantity = quantity
        self.is_available = is_available
        self.is_done = is_done

    def __unicode__(self):
        if not self.is_available:
            x_reason = "Out of stock"
        elif not self.is_done:
            x_reason = "Not finish order"
        else:
            x_reason = ""

        return u'{available_ic} {dish_name}{quantity_str}'.format(
            available_ic=':white_check_mark:' if not x_reason else ':x: ({}) '.format(x_reason),
            dish_name=self.dish_name,
            quantity_str='x{}'.format(self.quantity) if self.quantity > 1 else ''
        )


class MySlackClient:
    API_TOKEN = config.SLACK_TEST_TOKEN
    OWNER_USER_ID = config.OWNER_USER_ID

    def __init__(self):
        self.client = SlackClient(self.API_TOKEN)
        self.TEAMX_ID = Env.teamx_id

    def send_message(self, to, content):
        return self.client.api_call(
            'chat.postMessage',
            channel=to,
            username="Lunch Order Bot",
            icon_url="http://bit.ly/2zZ5Kp7",
            text=content,
        )

    def send_error(self, exception):
        return self.send_message(self.OWNER_USER_ID, """
:x: Lunch ordering caught in exception
*message*: `{message}` 
*trace*: ```{trace}```
        """.format(
            message=exception.message,
            trace=traceback.format_exc()
        ))

    def send_notify(self, content):
        return self.send_message(self.TEAMX_ID, content)


class NowClient:
    def __init__(self):
        self.email = config.EMAIL
        self.password = base64.decodestring(config.ENCODED_PASSWORD)
        self.user_id = None

        self.login_url = 'https://id.foody.vn/dang-nhap'
        self.get_share_link_endpoint = 'https://www.now.vn/Order/GetShareLink'
        self.get_cart_item_endpoint = 'https://www.now.vn/Order/LoadCartItem'
        self.get_cart_member_endpoint = 'https://www.now.vn/Order/GetShoppingCartMember'

        self.merchant_uri = Env.merchant.uri
        self.merchant_id = Env.merchant.id

        self.session = requests.Session()
        self.client_obj = None

    @staticmethod
    def get_client():
        now_client = NowClient()
        now_client.authenticate_session()
        now_client.obtain_user_id()
        return now_client

    def authenticate_session(self):
        # login https://id.foody.vn/dang-nhap
        form_data = {
            'Email': self.email,
            'Password': self.password,
            'RememberMe': True,
        }

        login_response = self.session.post(self.login_url, data=form_data)
        if login_response.status_code != 200:
            raise Exception('Failed to login id.foody.vn (1st step)')
        login_response_raw_content = login_response.content

        # parse validate url
        try:
            search_pattern = r'\"(https\:\/\/www\.now\.vn\:443.*?)\"'
            validate_uri = re.search(search_pattern, login_response_raw_content).group(1)
        except IndexError:
            raise Exception(
                'Failed to parse validate uri in login response (response: %s)' % login_response_raw_content)

        # validate token --> token returned in Cookie
        validate_response = self.session.get(validate_uri)
        validate_result = validate_response.content
        if validate_response.status_code != 200 or validate_result != 'done':
            raise Exception('Failed to validate token')

    def obtain_user_id(self):
        response = self.session.post(self.get_share_link_endpoint, json={"deliveryId": self.merchant_id})
        if response.status_code == 200:
            self.user_id = self.session.cookies.get('hostId')
            if not self.user_id:
                raise Exception('User id not found despite request is ok!')
        else:
            raise Exception('Failed to obtain user_id')

    def get_order(self):
        response = self.session.post(self.get_share_link_endpoint, json={"deliveryId": self.merchant_id})

        if response.status_code == 200:
            response_data = response.json()
            if response_data.get('success', False):
                order = Order(self.merchant_id, self.merchant_uri, response_data['data'])
                # Set shareCardId in cookie
                self.session.get(order.url())
                order.share_cart_id = self.session.cookies.get('shareCartId')
                return order

        return Order("error", self.merchant_uri, "error")

    def _get_is_done_status_by_user_name(self, order):
        body = {
            "deliveryId": order.merchant_id,
            "shareCartId": order.share_cart_id,
            "hostId": self.user_id,
        }
        response = self.session.post(self.get_cart_member_endpoint, json=body)
        status_by_name = {}

        if response.status_code == 200:
            response_data = response.json()
            if response_data.get('success', False):
                for item in response_data.get('data', []):
                    status_by_name[item.get('DisplayName')] = item.get('IsHost', False) or item.get('IsDone', False)
        return status_by_name

    def _get_availability_by_dish_id(self, list_all_item_cart_resp):
        is_available_by_dish_id = {}
        for item in list_all_item_cart_resp:
            is_available_by_dish_id[item.get('DishId')] = not item.get('OutOfStock', True)
        return is_available_by_dish_id

    def get_current_cart_list(self, order):
        body = {
            "deliveryId": order.merchant_id,
            "sharecode": order.share_code,
            "shareCartId": order.share_cart_id,
            "hostId": self.user_id,
        }
        response = self.session.post(self.get_cart_item_endpoint, json=body)
        cart_items = []

        if response.status_code == 200:
            response_data = response.json()
            if response_data.get('success', False):
                is_done_status_by_name = self._get_is_done_status_by_user_name(order)
                availability_by_dish_id = self._get_availability_by_dish_id(response_data.get('listAllItemCart', []))

                for item in response_data.get('data', []):
                    cart_items.append(CartItem(dish_name=item.get('DishName'),
                                               owner_name=item.get('OwerName'),
                                               quantity=item.get('Qty'),
                                               is_available=availability_by_dish_id.get(item.get('DishId')),
                                               is_done=is_done_status_by_name.get(item.get('OwerName'))))
        return cart_items


def run_job_at_time(hour, minute, job, *args, **kwargs):
    cur_hour = datetime.now().hour
    cur_min = datetime.now().minute
    if cur_hour == hour and cur_min == minute:
        return job(*args, **kwargs)
    else:
        return None


def get_next_weekday(check_date):
    date_delta = 1
    if check_date.weekday() >= 4:  # Friday
        date_delta = 7 - check_date.weekday()
    return check_date + timedelta(days=date_delta)


def remind_lunch_order_job(last_remind=False):
    sc = MySlackClient()

    now_client = NowClient.get_client()
    order_obj = now_client.get_order()

    today = date.today()

    message = """
Hi guys :robot_face:
{last_remind_option} Make sure to place your lunch order for `{order_date}` before `11:00am`
{icon} {order_link}
    """.format(
        order_date=today.strftime("%b %d"),
        order_link=order_obj.url(),
        last_remind_option="Last chance." if last_remind else "",
        icon=Env.merchant.ic
    )

    sc.send_notify(message)


def announce_next_lunch_order_job():
    sc = MySlackClient()

    now_client = NowClient.get_client()
    order_obj = now_client.get_order()

    today = date.today()
    next_order_date = get_next_weekday(today)

    message = """
Hi guys :robot_face:
New lunch order link for `{order_date}` is available,
{icon} {order_link}

Check your lunch payment here: 
:moneybag: http://bit.ly/2LvoMrB
""".format(
        order_date=next_order_date.strftime("%b %d"),
        order_link=order_obj.url(),
        icon=Env.merchant.ic
    )

    sc.send_notify(message)


def notify_current_cart_job():
    sc = MySlackClient()

    now_client = NowClient.get_client()
    order_obj = now_client.get_order()
    cart_items = now_client.get_current_cart_list(order_obj)

    messages = []
    items_by_name = {}
    for item in cart_items:
        items_by_name.setdefault(item.owner_name, []).append(item)

    for owner_name, items in items_by_name.iteritems():
        messages.append(u"- *{}*:\n{}".format(owner_name, "\n".join(map(unicode, items))))

    sc.send_notify(u"""
:blank:
:blank::blank::blank:{} Current Lunch Cart  <!here>
:blank:
{}
""".format(Env.merchant.ic, "\n\n".join(messages) if messages else u"`empty_cart`"))


def hello_world():
    sc = MySlackClient()
    sc.send_notify("Hello World :wave:")


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--env', default='test', choices=('test', 'prd'),
                        help='Environment to run, including merchant and notify Slack channel')
    parser.add_argument('--func', default=None, choices=(None,
                                                         'hello_world',
                                                         'announce_next_lunch_order_job',
                                                         'remind_lunch_order_job',
                                                         'notify_current_cart_job',
                                                         ))
    args = parser.parse_args()

    set_up_env(args.env)

    slack_client = MySlackClient()
    try:
        run_job_at_time(10, 0, remind_lunch_order_job)
        run_job_at_time(10, 30, remind_lunch_order_job, last_remind=True)
        run_job_at_time(10, 45, notify_current_cart_job)
        run_job_at_time(13, 0, announce_next_lunch_order_job)

        if args.func:
            func = args.func
            func = getattr(sys.modules[__name__], func)
            func()
    except Exception as e:
        slack_client.send_error(e)
