# helium\_hotspot\_exporter
Prometheus exporter for [Helium hotspots](https://helium.com/). Using prometheus\_client, this code exposes metrics from the helium API to a prometheus compatible server. 

This is only the exporter, which still requires a **prometheus server** for data and **grafana** for the dashboard.

<!--
todo: need a dashboard for this.

The [helium\_miner\_grafana\_dashboard](https://github.com/tedder/helium_miner_grafana_dashboard) can be imported to Grafana.
-->

Note [port 9829 is the 'reserved' port for this specific exporter](https://github.com/prometheus/prometheus/wiki/Default-port-allocations).


## Run options

The list of hotspots you'd like to monitor can be specified several ways: by name, by address, or by owner address. They all expect comma-separated values (or zero/one without a comma). Here are examples:
```
HOTSPOT_NAMES="angry-purple-tiger,bald cat lace"
HOTSPOT_ADDRESSES=aabbccdd112233XXYY...,bbccddee223344YYZZ...
OWNER_ADDRESSES=Zzyzx0123...
```

Note hotspot names must be lowercased and hypenated before they hit the API. This code will normalize case and spaces. Names are also not guaranteed unique, the code will attempt to log an error if it isn't found or it isn't unique.


## Running via Docker
Using the docker file, you can run this with Docker or docker-compose! Both of these will expose Prometheus on port 9829. The images are hosted on both [GHCR](https://github.com/users/tedder/packages/container/package/helium_hotspot_exporter) and [Dockerhub](https://hub.docker.com/r/tedder42/helium_hotspot_exporter).

### Docker client
```
docker run -p 9829:9829 -e HOTSPOT_NAMES=angry-purple-tiger --name hotspot_exporter ghcr.io/tedder/helium_hotspot_exporter:latest
```

### Docker-Compose
Using your existing docker-compose file, add the section for the exporter (below). When you're done, run `docker-compose up -d` as usual. That's it!
```
version: "3"
services:
  hotspot_exporter:
    image: ghcr.io/tedder/helium_hotspot_exporter:latest
    container_name: hotspot_exporter
    ports:
    - "9829:9829"
    env:
      HOTSPOT_NAMES: "angry-purple-tiger"
      HOTSPOT_ADDRESSES: "aabbccdd112233XXYY...,bbccddee223344YYZZ..."
      OWNER_ADDRESSES: "Zzyzx0123..."
```

