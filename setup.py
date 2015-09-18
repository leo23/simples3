#!/usr/bin/env python

from setuptools import setup

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
import simpleoss
intro = open("README", "U").read()
usage = "\nUsage\n-----\n\n" + simpleoss.__doc__
changes = open("changes.rst", "U").read()
long_description = intro + usage + "\n" + changes

setup(name="simpleoss", version=simpleoss.__version__,
      url="http://oss.aliyun.com",
      author="Leo", author_email="liuhuang6398@sohu.com",
      description="Simple, quick Aliyun OSS interface",
      long_description=long_description,
      packages=["simpleoss"])
