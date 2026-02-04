
FROM python:3.6-slim

COPY . /ot2protocols_package

WORKDIR ot2protocols_package

RUN python3 -m pip install -r requirements.txt

RUN python3 setup.py install

WORKDIR ot2protocols

CMD ["gunicorn", "--log-level", "debug", "--bind", "0.0.0.0:5000", "wsgi:application"]

EXPOSE 5000
