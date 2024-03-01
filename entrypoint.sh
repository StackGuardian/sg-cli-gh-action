#!/bin/sh -l

# Print a message indicating the execution of sg-cli command
echo "Executing sg-cli command: $@"

# Execute sg-cli command
sg-cli $@

# Check the exit status of the previous command
if [ $? -eq 0 ]; then
    # If the command was successful, print a success message
    echo "Command executed successfully!"
else
    # If the command failed, print an error message
    echo "Error: Command execution failed!"
fi


