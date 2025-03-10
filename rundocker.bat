docker run -d --restart unless-stopped \
  -p 3000:3000 \
  -v "/Users/icondor/Downloads/IOANAHR/Angajati SDL.xlsx:/app/Angajati SDL.xlsx" \
  -v "/Users/icondor/Downloads/IOANAHR/attendance_db:/app" \
  attendance-server
