import setuptools


setuptools.setup(
    name="ot2protocols",
    version="0.1.2",
    author="Nick Emery",
    packages=setuptools.find_packages(),
    description="OT2 workflows for Biocorp.",
    long_description_content_type="text/markdown",
    url="https://github.com/Biocorp/ot2protocols",
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        "flask",
        "gunicorn",
        "wtforms"
    ],
    test_suite='tests',
    setup_requires=["pytest-runner"],
    tests_require=["pytest"]
)
