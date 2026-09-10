# docker/

Container assets. Currently the only service is PostGIS, which uses the upstream
`postgis/postgis:16-3.4` image directly and needs no Dockerfile — see
`docker-compose.yml` and `database/init/`.

Images for the backend, the frontend and a SUMO worker will land here when
there is something worth containerising. Building them now would mean maintaining
Dockerfiles for services whose dependencies are still changing.

Local development runs the backend and frontend natively; see the root README.
