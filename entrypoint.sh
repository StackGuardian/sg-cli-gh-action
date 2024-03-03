#!/bin/sh -l

# Print a message indicating the execution of sg-cli command
echo "Executing: sg-cli $@"

# Execute sg-cli command
sg-cli $@
