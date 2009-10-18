"""Bucket manipulation"""

import time
import hmac
import hashlib
import re
import httplib
import urllib
import urllib2
import datetime
import mimetypes

rfc822_fmt = '%a, %d %b %Y %H:%M:%S GMT'
iso8601_fmt = '%Y-%m-%dT%H:%M:%S.000Z'

__version__ = "0.5"

def _amz_canonicalize(headers):
    r"""Canonicalize AMZ headers in that certain AWS way.

    >>> _amz_canonicalize({"x-amz-test": "test"})
    'x-amz-test:test\n'
    >>> _amz_canonicalize({"x-amz-first": "test",
    ...                    "x-amz-second": "hello"})
    'x-amz-first:test\nx-amz-second:hello\n'
    >>> _amz_canonicalize({})
    ''
    """
    rv = {}
    for header, value in headers.iteritems():
        header = header.lower()
        if header.startswith("x-amz-"):
            rv.setdefault(header, []).append(value)
    parts = []
    for key in sorted(rv):
        parts.append("%s:%s\n" % (key, ",".join(rv[key])))
    return "".join(parts)

def metadata_headers(metadata):
    return dict(("X-AMZ-Meta-" + h, v) for h, v in metadata.iteritems())

def headers_metadata(headers):
    return dict((h[11:], v) for h, v in headers.iteritems()
                            if h.lower().startswith("x-amz-meta-"))

def _rfc822_dt(v): return datetime.datetime.strptime(v, rfc822_fmt)
def _iso8601_dt(v): return datetime.datetime.strptime(v, iso8601_fmt)

def aws_md5(data):
    """Make an AWS-style MD5 hash (digest in base64).

    >>> aws_md5("Hello!")
    'lS0sVtBIWVgzZ0e83ZhZDQ=='
    """
    return hashlib.md5(data).digest().encode("base64")[:-1]

def aws_urlquote(value):
    r"""AWS-style quote a URL part.

    >>> aws_urlquote("/bucket/a key")
    '/bucket/a%20key'
    >>> aws_urlquote(u"/bucket/\xe5der")
    '/bucket/%C3%A5der'
    """
    if isinstance(value, unicode):
        value = value.encode("utf-8")
    return urllib.quote(value, "/")

def guess_mimetype(fn, default="application/octet-stream"):
    """Guess a mimetype from filename *fn*."""
    if "." not in fn:
        return default
    bfn, ext = fn.lower().rsplit(".", 1)
    if ext == "jpg": ext = "jpeg"
    return mimetypes.guess_type(bfn + "." + ext)[0] or default

def info_dict(headers):
    rv = {"headers": headers, "metadata": headers_metadata(headers)}
    if "content-length" in headers:
        rv["size"] = int(headers["content-length"])
    if "content-type" in headers:
        rv["mimetype"] = headers["content-type"]
    if "date" in headers:
        rv["date"] = _rfc822_dt(headers["date"]),
    if "last-modified" in headers:
        rv["modify"] = _rfc822_dt(headers["last-modified"])
    return rv

def name(o):
    """Find the name of *o*.

    Functions:
    >>> name(name)
    'name'
    >>> def my_fun(): pass
    >>> name(my_fun)
    'my_fun'

    Classes:
    >>> name(Exception)
    'exceptions.Exception'
    >>> class MyKlass(object): pass
    >>> name(MyKlass)
    'MyKlass'

    Instances:
    >>> name(Exception()), name(MyKlass())
    ('exceptions.Exception', 'MyKlass')

    Types:
    >>> name(str), name(object), name(int)
    ('str', 'object', 'int')

    Type instances:
    >>> name("Hello"), name(True), name(None), name(Ellipsis)
    ('str', 'bool', 'NoneType', 'ellipsis')
    """
    if hasattr(o, "__name__"):
        rv = o.__name__
        modname = getattr(o, "__module__", None)
        # This work-around because Python does it itself,
        # see typeobject.c, type_repr.
        # Note that Python only checks for __builtin__.
        if modname and modname[:2] + modname[-2:] != "____":
            rv = o.__module__ + "." + rv
    else:
        for o in getattr(o, "__mro__", o.__class__.__mro__):
            rv = name(o)
            # If there is no name for the this baseclass, this ensures we check
            # the next rather than say the object has no name (i.e., return
            # None)
            if rv is not None:
                break
    return rv

class S3Error(Exception):
    def __init__(self, message, **kwds):
        self.args = message, kwds.copy()
        self.msg, self.extra = self.args

    def __str__(self):
        rv = self.msg
        if self.extra:
            rv += " ("
            rv += ", ".join("%s=%r" % i for i in self.extra.items())
            rv += ")"
        return rv

    @classmethod
    def from_urllib(cls, e):
        """Try to read the real error from AWS."""
        self = cls("HTTP error")
        for attr in "reason", "code", "filename":
            if hasattr(e, attr):
                self.extra[attr] = getattr(e, attr)
        fp = getattr(e, "fp", None)
        if not fp:
            return self
        self.fp = fp
        # The latter part of this clause is to avoid some weird bug in urllib2
        # and AWS which has it read as if chunked, and AWS gives empty reply.
        try:
            self.data = data = fp.read()
        except (httplib.HTTPException, urllib2.URLError), e:
            self.extra["read_error"] = e
        else:
            begin, end = data.find("<Message>"), data.find("</Message>")
            if min(begin, end) >= 0:
                self.full_message = msg = data[begin + 9:end]
                self.msg = msg[:100]
                if self.msg != msg:
                    self.msg += "..."
        return self

    @property
    def code(self): return self.extra.get("code")

class StreamHTTPHandler(urllib2.HTTPHandler):
    pass

class StreamHTTPSHandler(urllib2.HTTPSHandler):
    pass

class AnyMethodRequest(urllib2.Request):
    def __init__(self, method, *args, **kwds):
        self.method = method
        urllib2.Request.__init__(self, *args, **kwds)

    def get_method(self):
        return self.method

class S3File(str):
    def __new__(cls, value, **kwds):
        return super(S3File, cls).__new__(cls, value)

    def __init__(self, value, **kwds):
        kwds["data"] = value
        self.kwds = kwds

    def put_into(self, bucket, key):
        return bucket.put(key, **self.kwds)

class S3Bucket(object):
    amazon_s3_base = "https://s3.amazonaws.com/"
    listdir_re = re.compile(r"^<Key>(.+?)</Key>"
                            r"<LastModified>(.{24})</LastModified>"
                            r"<ETag>(.+?)</ETag><Size>(\d+?)</Size>$")

    def __init__(self, name, access_key=None, secret_key=None, base_url=None):
        self.opener = urllib2.build_opener(StreamHTTPHandler, StreamHTTPSHandler)
        self.name = name
        self.access_key = access_key
        self.secret_key = secret_key
        if not base_url:
            self.base_url = self.amazon_s3_base + aws_urlquote(name)
        else:
            self.base_url = base_url

    def __str__(self):
        return "<%s %s at %r>" % (self.__class__.__name__, self.name, self.base_url)

    def __repr__(self):
        return self.__class__.__name__ + "(%r, access_key=%r, base_url=%r)" % (
            self.name, self.access_key, self.base_url)

    def __getitem__(self, name): return self.get(name)
    def __delitem__(self, name): return self.delete(name)
    def __setitem__(self, name, value):
        if hasattr(value, "put_into"):
            return value.put_into(self, name)
        else:
            return self.put(name, value)
    def __contains__(self, name):
        try:
            self.info(name)
        except KeyError:
            return False
        else:
            return True

    def sign_description(self, desc):
        """AWS-style sign data."""
        hasher = hmac.new(self.secret_key, desc.encode("utf-8"), hashlib.sha1)
        return hasher.digest().encode("base64")[:-1]

    def make_description(self, method, key=None, data=None,
                         headers={}, subresource=None, bucket=None):
        # The signature descriptor is detalied in the developer's PDF on p. 65.
        res = self.canonicalized_resource(key, bucket=bucket)
        # Append subresource, if any.
        if subresource:
            res += "?" + subresource
        # Make description. :/
        return "\n".join((method, headers.get("Content-MD5", ""),
            headers.get("Content-Type", ""), headers.get("Date", ""))) + "\n" +\
            _amz_canonicalize(headers) + res

    def canonicalized_resource(self, key, bucket=None):
        res = "/"
        if bucket or bucket is None:
            res += aws_urlquote(bucket or self.name)
        res += "/"
        if key:
            res += aws_urlquote(key)
        return res

    def get_request_signature(self, method, key=None, data=None,
                              headers={}, subresource=None, bucket=None):
        return self.sign_description(self.make_description(method, key=key,
            data=data, headers=headers, subresource=subresource, bucket=bucket))

    def new_request(self, method, key=None, args=None, data=None, headers={}):
        headers = headers.copy()
        if data and "Content-MD5" not in headers:
            headers["Content-MD5"] = aws_md5(data)
        if "Date" not in headers:
            headers["Date"] = time.strftime(rfc822_fmt, time.gmtime())
        if "Authorization" not in headers:
            sign = self.get_request_signature(method, key=key, data=data,
                                              headers=headers)
            headers["Authorization"] = "AWS %s:%s" % (self.access_key, sign)
        url = self.make_url(key, args)
        return AnyMethodRequest(method, url, data=data, headers=headers)

    def make_url(self, key, args=None, arg_sep=";"):
        url = self.base_url + "/"
        if key:
            url += aws_urlquote(key)
        if args:
            if hasattr(args, "iteritems"):
                args_items = args.iteritems()
            elif hasattr(args, "items"):
                args_items = args.items()
            else:
                args_items = args
            url += "?" + arg_sep.join("=".join(map(urllib.quote_plus, item))
                                      for item in args_items)
        return url

    def open_request(self, request, errors=True):
            return self.opener.open(request)

    def make_request(self, method, key=None, args=None, data=None, headers={}):
        for retry_no in xrange(10):
            request = self.new_request(method, key=key, args=args,
                                       data=data, headers=headers)
            try:
                return self.open_request(request)
            except (urllib2.HTTPError, urllib2.URLError), e:
                # If S3 gives HTTP 500, we should try again.
                if getattr(e, "code", None) == 500:
                    continue
                raise S3Error.from_urllib(e)
        else:
            raise RuntimeError("ran out of retries")  # Shouldn't happen.

    def get(self, key):
        response = self.make_request("GET", key=key)
        response.s3_info = info_dict(dict(response.info()))
        return response

    def info(self, key):
        try:
            response = self.make_request("HEAD", key=key)
        except S3Error, e:
            if e.code == 404:
                raise KeyError(key)
            raise e
        rv = info_dict(dict(response.info()))
        response.close()
        return rv

    def put(self, key, data=None, acl=None, metadata={}, mimetype=None,
            transformer=None, headers={}):
        headers = headers.copy()
        headers.update({"Content-Type": mimetype or guess_mimetype(key)})
        headers.update(metadata_headers(metadata))
        if acl: headers["X-AMZ-ACL"] = acl
        if transformer: data = transformer(headers, data)
        headers.update({"Content-Length": str(len(data)),
                        "Content-MD5": aws_md5(data)})
        self.make_request("PUT", key=key, data=data, headers=headers).close()

    def delete(self, key):
        # In <=py25, urllib2 raises an exception for HTTP 204, and later
        # does not, so treat errors and non-errors as equals.
        try:
            resp = self.make_request("DELETE", key=key)
        except S3Error, e:
            resp = e
        resp.close()
        if 200 <= resp.code < 300:
            return True
        elif resp.code == 404:
            raise KeyError(key)
        else:
            raise S3Error.from_urllib(resp)

    # TODO Expose the conditional headers, x-amz-copy-source-if-*
    # TODO Add module-level documentation and doctests.
    def copy(self, source, key, acl=None, metadata=None,
             mimetype=None, headers={}):
        """Copy S3 file *source* on format '<bucket>/<key>' to *key*.

        If metadata is not None, replaces the metadata with given metadata,
        otherwise copies the previous metadata.

        Note that *acl* is not copied, but set to *private* by S3 if not given.
        """
        headers = headers.copy()
        headers.update({"Content-Type": mimetype or guess_mimetype(key)})
        headers["X-AMZ-Copy-Source"] = source
        if acl: headers["X-AMZ-ACL"] = acl
        if metadata is not None:
            headers["X-AMZ-Metadata-Directive"] = "REPLACE"
            headers.update(metadata_headers(metadata))
        else:
            headers["X-AMZ-Metadata-Directive"] = "COPY"
        self.make_request("PUT", key=key, headers=headers).close()

    def listdir(self, prefix=None, marker=None, limit=None, delimiter=None):
        """List contents of bucket.

        *prefix*, if given, predicates `key.startswith(prefix)`.
        *marker*, if given, predicates `key > marker`, lexicographically.
        *limit*, if given, predicates `len(keys) <= limit`.
        """
        mapping = (("prefix", prefix),
                   ("marker", marker),
                   ("max-keys", limit),
                   ("delimiter", delimiter))
        args = dict((k, v) for (k, v) in mapping if v is not None)
        response = self.make_request("GET", args=args)
        buffer = ""
        while True:
            data = response.read(4096)
            buffer += data
            while True:
                pos_end = buffer.find("</Contents>")
                if pos_end == -1:
                    break
                piece = buffer[buffer.index("<Contents>") + 10:pos_end]
                buffer = buffer[pos_end + 10:]
                info = piece[:piece.index("<Owner>")]
                mo = self.listdir_re.match(info)
                if not mo:
                    raise ValueError("unexpected: %r" % (piece,))
                key, modify, etag, size = mo.groups()
                # FIXME A little brittle I would say...
                etag = etag.replace("&quot;", '"')
                yield key, _iso8601_dt(modify), etag, int(size)
            if not data:
                break

    @staticmethod
    def _now():
        """
        Wraps datetime.now() for testability.
        """
        return datetime.datetime.now()

    def url_for(self, key, authenticated=False,
                expire=datetime.timedelta(minutes=5)):
        """Produce the URL for given S3 object key.

        *key* specifies the S3 object path relative to the
            base URL of the bucket.
        *authenticated* asks for URL query string authentication
            to be used; such URLs include a signature and expiration
            time, and may be used by HTTP client apps to gain
            temporary access to private S3 objects.
        *expire* indicates when the produced URL ceases to function,
            in seconds from 1970-01-01T00:00:00 UTC; if omitted, the URL
            will expire in 5 minutes from now.

        URL for a publicly accessible S3 object:
        >>> S3Bucket("bottle").url_for("the dregs")
        'https://s3.amazonaws.com/bottle/the%20dregs'

        Query string authenitcated URL example (fake S3 credentials are shown):
        >>> b = S3Bucket("johnsmith", access_key="0PN5J17HBGZHT7JJ3X82",
        ...     secret_key="uV3F3YluFJax1cknvbcGwgjvx4QpvB+leU8dUj2o")
        >>> b.url_for("foo.js", authenticated=True) # doctest: +ELLIPSIS
        'https://s3.amazonaws.com/johnsmith/foo.js?AWSAccessKeyId=...&Expires=...&Signature=...'
        """
        if authenticated:
            if not hasattr(expire, "timetuple"):
                # XXX isinstance, but who uses UNIX timestamps?
                if isinstance(expire, (int, long)):
                    expire = datetime.datetime.fromtimestamp(expire)
                else:
                    # Assume timedelta.
                    expire = self._now() + expire
            expire_desc = str(long(time.mktime(expire.timetuple())))
            auth_descriptor = "".join((
                "GET\n",
                "\n",
                "\n",
                expire_desc + "\n",
                self.canonicalized_resource(key)  # No "\n" by design!
            ))
            args = (("AWSAccessKeyId", self.access_key),
                    ("Expires", expire_desc),
                    ("Signature", self.sign_description(auth_descriptor)))
            return self.make_url(key, args, "&")
        else:
            return self.make_url(key)

    def put_bucket(self, config_xml=None, acl=None):
        if config_xml:
            headers = {"Content-Length": len(config_xml),
                       "Content-Type": "text/xml"}
        else:
            headers = {"Content-Length": "0"}
        if acl:
            headers["X-AMZ-ACL"] = acl
        resp = self.make_request("PUT", key=None, data=config_xml, headers=headers)
        resp.close()
        return resp.code == 200

    def delete_bucket(self):
        return self.delete(None)