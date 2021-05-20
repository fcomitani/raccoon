from setuptools import setup, find_packages
from codecs import open
from os import path

here = path.abspath(path.dirname(__file__))

with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(

    name='coon',

    version='0.4.0',

    description='Recursive clustering in Python',
    long_description=long_description,

    url='https://github.com/fcomitani/raccoon',
	download_url = 'https://github.com/fcomitani/raccoon/archive/0.4.0.tar.gz', 
    author='Federico Comitani',
    author_email='federico.comitani@gmail.com',

    license='MIT',

    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Science/Research',
        'Topic :: Scientific/Engineering',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python'
        'Programming Language :: Python :: 3.7'
		],

    keywords='clustering recursion dimension-reduction k-NN hiearchical-clustering optimal-clusters differential-evolution',

    packages=find_packages(exclude=['contrib', 'docs', 'tests']),

    install_requires=['numpy>=1.20.2',
		'pandas>=1.1.3',
		'scikit-learn>=0.22.2.post1',
		'scikit-network>=0.20.0',
		'umap-learn>=0.4.5',
		'psutil>=5.7.3',
		'anytree>=2.8.0',
		'matplotlib>=3.3.3',
		'seaborn>=0.11.0'],

    extras_require={ 'gpu': ['cupy==8.60',
				'cuml==0.18',
				'cudf==0.18',
				'cugraph==0.18'],
		     'hdbscan': ['hdbscan']},
    zip_safe=False,

)

