"""
Setup script for RHOAI Test Failure Analyzer

Installs the rhoai-tfa command-line tool.
"""
from setuptools import setup, find_packages
from pathlib import Path

# Read long description from README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="rhoai-tfa",
    version="2.0.0",
    description="RHOAI/ODH Test Failure Analyzer - Multi-component test analysis platform",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="RHOAI QE Team",
    author_email="rhoai-qe@redhat.com",
    url="https://github.com/antowaddle/odh-pipeline-test-analyzer",

    packages=find_packages(include=['analyzer', 'analyzer.*']),

    # Include non-Python files
    include_package_data=True,

    # Dependencies
    install_requires=[
        "httpx>=0.24.0",
        "python-dotenv>=1.0.0",
    ],

    # Optional dependencies
    extras_require={
        'dev': [
            'pytest>=7.0.0',
            'pytest-asyncio>=0.21.0',
        ],
    },

    # Command-line scripts
    entry_points={
        'console_scripts': [
            'rhoai-tfa=scripts.rhoai_tfa:main',
        ],
    },

    # Python version requirement
    python_requires='>=3.9',

    # Classifiers
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Testing',
        'Topic :: Software Development :: Quality Assurance',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
    ],

    # Keywords
    keywords='testing qa jenkins test-analysis rhoai odh',
)
