#!/bin/bash
cd /Users/shangguan/Softwares/my_modules/ArXistant
python3 src/arxiv_db_server.py > local/server.log 2>&1 &
echo $!
