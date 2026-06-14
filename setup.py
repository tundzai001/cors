from setuptools import setup, Extension
module1 = Extension('demo', sources = ['demo.c'])
setup (name = 'PackageName', version = '1.0', ext_modules = [module1])
