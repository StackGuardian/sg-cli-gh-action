# Container image that runs your code
FROM alpine:3.10

# Install necessary tools
RUN apk --no-cache add bash wget jq curl

# Download and extract StackGuardian CLI release
RUN wget -q "$(wget -qO- "https://api.github.com/repos/stackguardian/sg-cli/releases/latest" | jq -r '.tarball_url')" -O sg-cli.tar.gz \
    && tar -xf sg-cli.tar.gz \
    && rm -f sg-cli.tar.gz \
    && mv StackGuardian-sg-cli*/sg-cli /usr/local/bin/ \
    && rm -rf StackGuardian-sg-cli*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
