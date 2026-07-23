# Use a slim base for speed and security
# *Build this once:*
#   podman build -t toolex-sandbox .
g
FROM debian:bookworm-slim

# Install only the essentials required by your tools
# Disable APT sandboxing via configuration flag during installation
RUN apt-get -o APT::Sandbox::User=root update && \
    apt-get -o APT::Sandbox::User=root install -y \
    bash \
    git \
    procps \
    grep \
    findutils \
    diffutils \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory to where we will mount the user's data
WORKDIR /workspace

# Run as a non-root user for extra security
RUN useradd -m sandboxuser
USER sandboxuser

# Default command is just bash
CMD ["/bin/bash"]
