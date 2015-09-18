#simpleoss

##Overview
A fairly simple, decently quick interface to Aliyun's OSS storage service.

It grew out of frustration with other libraries that were either written too
pragmatically (slow), too bloatedly, or just half-done.

The module aims for:

 * simplicity,
 * decent speed,
 * non-intrusiveness.

It really is designed to fit into programmer memory. The three basic operations
are as easy as with dictionaries.

##Dependencies

Requires **Python 2.5+** and **nose** for running tests. Python 3 support is not yet available. Apart from that, the code relies solely on Python
standard libraries.

##Installation

```sh
pip install simpleoss
```

##Usage

Access to a bucket is done via the OSSBucket class. It has three required arguments:

```python
from simpleoss.bucket import OSSBucket

s = OSSBucket(bucket,
             access_key=access_key,
             secret_key=secret_key)
 
print s  
#<OSSBucket ... at 'http://oss.aliyun.com/...'>
```
To add a file, simply do
```python
s.put("my file", "my content")
```
To retrieve a file do 
```python
f = s.get("my file")
print f.read()
#my content
```
To retrieve information about a file, do
```python
print f.oss_info["mimetype"]
#'application/octet-stream'

print f.oss_info.keys()
#['mimetype', 'modify', 'headers', 'date', 'size', 'metadata']
```
To delete a file, do
```python
del s["my file!"]
```

##Contributing

###IRC
``#sendapatch`` on ``chat.freenode.net``.
