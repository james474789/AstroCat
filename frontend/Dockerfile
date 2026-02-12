# ===========================================
# AstroCat Frontend Dockerfile
# ===========================================

# -------------------------------------------
# Development Stage
# -------------------------------------------
FROM node:20-alpine AS development

WORKDIR /app

# Install dependencies first (for better caching)
# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

# Copy source code
COPY . .

# Expose Vite dev server port
EXPOSE 8090

# Development command
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8090"]

# -------------------------------------------
# Build Stage
# -------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Add build argument for Vite
ARG VITE_API_URL
ARG VITE_LOGO_TITLE
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_LOGO_TITLE=$VITE_LOGO_TITLE

# Debug: Print the VITE_API_URL value
RUN echo "Building with VITE_API_URL=${VITE_API_URL}"

# Install dependencies
# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

# Copy source and build
COPY . .

# Create .env.production file so Vite picks up the variable
RUN echo "VITE_API_URL=${VITE_API_URL}" > .env.production
RUN echo "VITE_LOGO_TITLE=${VITE_LOGO_TITLE}" >> .env.production
RUN cat .env.production

RUN npm run build

# -------------------------------------------
# Production Stage (nginx)
# -------------------------------------------
FROM nginx:alpine AS production

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 8090

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
