#!/bin/sh -l
set +x
printenv SG_API_TOKEN
sg-cli $@
time=$(date)
echo "time=$time"

