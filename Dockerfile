# Container image that runs your code
FROM alpine:3.10

RUN apk add --no-cache bash
# Install necessary tools
RUN apk --no-cache add wget jq curl

# Download and extract StackGuardian CLI release
RUN wget -q "$(wget -qO- "https://api.github.com/repos/stackguardian/sg-cli/releases/latest" | jq -r '.tarball_url')" -O sg-cli.tar.gz \
    && tar -xf sg-cli.tar.gz \
    && rm -f sg-cli.tar.gz \
    && mv StackGuardian-sg-cli*/sg-cli /usr/local/bin/ \
    && rm -rf StackGuardian-sg-cli*

# Copies your code file from your action repository to the filesystem path `/` of the container
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x entrypoint.sh
# Code file to execute when the docker container starts up (`entrypoint.sh`)
CMD ["/entrypoint.sh"]

