FROM python:3-buster
LABEL org.opencontainers.image.source https://github.com/tedder/helium_hotspot_exporter
ENV PYTHONUNBUFFERED=1
ENV HOTSPOT_NAMES=
ENV HOTSPOT_ADDRESSES=
ENV OWNER_ADDRESSES=
EXPOSE 9825

#RUN apt update && apt install -y vim
COPY requirements.txt /opt/app/
RUN pip3 install -r /opt/app/requirements.txt

# copying the py later than the reqs so we don't need to rebuild as often
COPY *py /opt/app/
CMD /opt/app/helium_hotspot_exporter.py
