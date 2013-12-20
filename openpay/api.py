import datetime
import calendar
import time
import warnings
import urllib
import urlparse
import platform
import json

import openpay
from openpay import error, http_client, version, util


def _encode_datetime(dttime):
    if dttime.tzinfo and dttime.tzinfo.utcoffset(dttime) is not None:
        utc_timestamp = calendar.timegm(dttime.utctimetuple())
    else:
        utc_timestamp = time.mktime(dttime.timetuple())

    return int(utc_timestamp)


def _api_encode(data):
    for key, value in data.iteritems():
        key = util.utf8(key)
        if value is None:
            continue
        elif hasattr(value, 'openpay_id'):
            yield (key, value.openpay_id)
        elif isinstance(value, list) or isinstance(value, tuple):
            for subvalue in value:
                yield ("%s[]" % (key,), util.utf8(subvalue))
        elif isinstance(value, dict):
            subdict = dict(('%s[%s]' % (key, subkey), subvalue) for
                           subkey, subvalue in value.iteritems())
            for subkey, subvalue in _api_encode(subdict):
                yield (subkey, subvalue)
        elif isinstance(value, datetime.datetime):
            yield (key, _encode_datetime(value))
        else:
            yield (key, util.utf8(value))


def _build_api_url(url, query):
    scheme, netloc, path, base_query, fragment = urlparse.urlsplit(url)

    if base_query:
        query = '%s&%s' % (base_query, query)

    return urlparse.urlunsplit((scheme, netloc, path, query, fragment))


class APIClient(object):

    def __init__(self, key=None, client=None, test_mode=False):
        self.api_key = key

        from openpay import verify_ssl_certs
        openpay.test_mode = test_mode

        self._client = client or http_client.new_default_http_client(
            verify_ssl_certs=verify_ssl_certs)

    def request(self, method, url, params=None):
        rbody, rcode, my_api_key = self.request_raw(
            method.lower(), url, params)
        resp = self.interpret_response(rbody, rcode)
        return resp, my_api_key

    def handle_api_error(self, rbody, rcode, resp):
        try:
            err = resp['error']
        except (KeyError, TypeError):
            raise error.APIError(
                "Invalid response object from API: %r (HTTP response code "
                "was %d)" % (rbody, rcode),
                rbody, rcode, resp)

            if rcode in [400, 404]:
                raise error.InvalidRequestError(
                    err.get('message'), err.get('param'), rbody, rcode, resp)
            elif rcode == 401:
                raise error.AuthenticationError(
                    err.get('message'), rbody, rcode, resp)
            elif rcode == 402:
                raise error.CardError(err.get('message'), err.get('param'),
                                      err.get('code'), rbody, rcode, resp)
            else:
                raise error.APIError(err.get('message'), rbody, rcode, resp)

    def request_raw(self, method, url, params=None):
        """
        Mechanism for issuing an API call
        """
        from openpay import api_version

        # Removing no needed params
        if 'customer' in params.keys():
            del params['customer']

        if self.api_key:
            my_api_key = self.api_key
        else:
            from openpay import api_key
            my_api_key = api_key

        if my_api_key is None:
            raise error.AuthenticationError(
                'No API key provided. (HINT: set your API key using '
                '"openpay.api_key = <API-KEY>"). You can generate API keys '
                'from the Stripe web interface.  See https://openpay.mx/api '
                'for details, or email support@openpay.mx if you have any '
                'questions.')

        abs_url = "{0}{1}".format(openpay.get_api_base(), url)

        encoded_params = json.dumps(params)

        if method == 'get' or method == 'delete':
            if params:
                abs_url = _build_api_url(abs_url, encoded_params)
            post_data = None
        elif method == 'post' or method == 'put':
            post_data = encoded_params
        else:
            raise error.APIConnectionError(
                'Unrecognized HTTP method %r.  This may indicate a bug in the '
                'Openpay bindings.  Please contact support@openpay.mx for '
                'assistance.' % (method,))

        ua = {
            'bindings_version': version.VERSION,
            'lang': 'python',
            'publisher': 'openpay',
            'httplib': self._client.name
        }
        for attr, func in [['lang_version', platform.python_version],
                          ['platform', platform.platform],
                          ['uname', lambda: ' '.join(platform.uname())]]:
            try:
                val = func()
            except Exception as e:
                val = "!! %s" % (e,)
            ua[attr] = val

        headers = {
            'X-Openpay-Client-User-Agent': json.dumps(ua),
            'User-Agent': 'Openpay/v1 PythonBindings/%s' % (version.VERSION,),
            'content-type': 'application/json',
        }

        if api_version is not None:
            headers['Openpay-Version'] = api_version

        rbody, rcode = self._client.request(
            method, abs_url, headers, post_data, user=my_api_key)

        util.logger.info(
            'API request to %s returned (response code, response body) of '
            '(%d, %r)',
            abs_url, rcode, rbody)
        return rbody, rcode, my_api_key

    def interpret_response(self, rbody, rcode):
        try:
            if hasattr(rbody, 'decode'):
                rbody = rbody.decode('utf-8')

            if rcode == 204:
                rbody = json.dumps({})

            resp = json.loads(rbody)
        except Exception:
            raise error.APIError(
                "Invalid response body from API: %s "
                "(HTTP response code was %d)" % (rbody, rcode),
                rbody, rcode)
        if not (200 <= rcode < 300):
            self.handle_api_error(rbody, rcode, resp)
        return resp