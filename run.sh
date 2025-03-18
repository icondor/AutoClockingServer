docker build -t attendance-server .

#Run the container, assumes colina on mac os running at startup
# docker run -d --restart unless-stopped -p 3001:3001 attendance-server
docker ps
