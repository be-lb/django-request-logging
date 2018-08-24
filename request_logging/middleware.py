import logging
import re
import json
from datetime import datetime

from django.conf import settings
from django.utils.termcolors import colorize
try:
    # Django 1.x
    from django.core.urlresolvers import resolve
except ImportError:
    # Django 2.x
    from django.urls import resolve

DEFAULT_LOG_LEVEL = logging.DEBUG
DEFAULT_COLORIZE = True
DEFAULT_MAX_BODY_LENGTH = 50000  # log no more than 3k bytes of content
SETTING_NAMES = {
    'log_level': 'REQUEST_LOGGING_DATA_LOG_LEVEL',
    'legacy_colorize': 'REQUEST_LOGGING_DISABLE_COLORIZE',
    'colorize': 'REQUEST_LOGGING_ENABLE_COLORIZE',
    'max_body_length': 'REQUEST_LOGGING_MAX_BODY_LENGTH'
}
BINARY_REGEX = re.compile(r'(.+Content-Type:.*?)(\S+)/(\S+)(?:\r\n)*(.+)', re.S | re.I)
BINARY_TYPES = ('image', 'application')
NO_LOGGING_ATTR = 'no_logging'
NO_LOGGING_MSG = 'No logging for this endpoint'
request_logger = logging.getLogger('django.request')


class Logger:
    def log(self, level, msg, logging_context):
        args = logging_context['args']
        kwargs = logging_context['kwargs']
        for line in re.split(r'\r?\n', str(msg)):
            request_logger.log(level, line, *args, **kwargs)

    def log_error(self, level, msg, logging_context):
        self.log(level, msg, logging_context)


class LoggingMiddleware(object):
    def __init__(self, get_response=None):
        self.get_response = get_response

        self.log_level = getattr(settings, SETTING_NAMES['log_level'], DEFAULT_LOG_LEVEL)
        if self.log_level not in [logging.NOTSET, logging.DEBUG, logging.INFO,
                                  logging.WARNING, logging.ERROR, logging.CRITICAL]:
            raise ValueError("Unknown log level({}) in setting({})".format(self.log_level, SETTING_NAMES['log_level']))

        self.logger = Logger()

    def __call__(self, request):
        start_time= datetime.utcnow()
        headers = self.get_request_headers(request)
        req_info = dict(
                method=request.method,
                path=request.path,
                user=request.user.username,
        )
        response = self.get_response( request )
        self.process_response( request, response , start_time, req_info, headers)
        return response

    def _should_log_route(self, request):
        try:
            route_match = resolve(request.path)
        except:
            return None

        method = request.method.lower()
        view = route_match.func
        func = view
        # This is for "django rest framework"
        if hasattr(view, 'cls'):
            if hasattr(view, 'actions'):
                actions = view.actions
                method_name = actions.get(method)
                if method_name:
                    func = getattr(view.cls, view.actions[method], None)
            else:
                func = getattr(view.cls, method, None)
        elif hasattr(view, 'view_class'):
            # This is for django class-based views
            func = getattr(view.view_class, method, None)
        no_logging = getattr(func, NO_LOGGING_ATTR, None)
        return no_logging

    def _skip_logging_request(self, request, reason):
        method_path = "{} {}".format(request.method, request.get_full_path())
        no_log_context = {
            'args': (),
            'kwargs': {
                'extra': {
                    'no_logging': reason
                },
            },
        }
        self.logger.log(logging.INFO, method_path + " (not logged because '" + reason + "')", no_log_context)

    def get_request_headers(self, request):
        headers = {k: v for k, v in request.META.items() if k.startswith('HTTP_')}
        return headers

    def process_response(self,request, response, start_time, req_info, headers):
        skip_logging_because = self._should_log_route(request)
        if skip_logging_because:
            return response
        logging_context = self._get_logging_context(request, response)
        delta = datetime.utcnow() - start_time
        
        message = dict(
                status_code=response.status_code,
                time=delta.total_seconds(),
        )
        message.update(req_info)
        message.update(headers)

        path = req_info['path']
        for i,p in enumerate(path.split('/')):
            pk = 'path_{}'.format(i)
            message[pk] = p 

        self.logger.log(logging.INFO, json.dumps(message), logging_context)

        return response

    def _get_logging_context(self, request, response):
        """
        Returns a map with args and kwargs to provide additional context to calls to logging.log().
        This allows the logging context to be created per process request/response call.
        """
        return {
            'args': (),
            'kwargs': {
                'extra': {
                    'request': request,
                    'response': response,
                },
            },
        }

