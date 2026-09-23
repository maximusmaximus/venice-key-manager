from setuptools import setup, find_packages

setup(
    name="venice-key-manager",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "fastapi>=0.110.0",
        "uvicorn[standard]>=0.28.0",
        "httpx>=0.27.0",
        "pydantic>=2.6.0",
        "python-dotenv>=1.0.1",
        "jinja2>=3.1.3",
        "python-multipart>=0.0.9"
    ],
    entry_points={
        "console_scripts": [
            "venice-key-manager=venice_key_manager.cli:main",
        ],
    },
)
