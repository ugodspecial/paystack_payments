from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = [
        line.strip() for line in f if line.strip() and not line.startswith("#")
    ]

setup(
    name="paystack_payments",
    version="1.0.0",
    description="Paystack payment gateway for Frappe — depends on the payments app, not ERPNext",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="YoungAndCode LTD",
    author_email="info@youngandcodeltd.com",
    url="https://github.com/ugodspecial/paystack_payments",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
    python_requires=">=3.10,<3.15",
    license="MIT",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Framework :: Frappe",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Office/Business :: Financial",
    ],
)
