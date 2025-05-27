/**
 * Socket Manager - Handles socket.io connections, reconnections, and session recovery
 *
 * This module provides robust connection management for the chat application,
 * ensuring that connections are maintained and recovered when interrupted.
 */

class SocketManager {
    constructor(options = {}) {
        // Default options
        this.options = {
            debug: false,
            autoReconnect: true,
            maxReconnectAttempts: 10,
            reconnectInterval: 3000,
            heartbeatInterval: 25000,
            ...options
        };

        // State variables
        this.socket = null;
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.reconnectTimer = null;
        this.heartbeatTimer = null;
        this.roomId = null;
        this.visitorId = null;
        this.adminId = null;
        this.lastMessageTimestamp = 0;
        this.pendingMessages = [];
        this.eventHandlers = {};
        this.connectionListeners = [];
        this.disconnectionListeners = [];
        this.reconnectionListeners = [];

        // Bind methods to maintain 'this' context
        this.connect = this.connect.bind(this);
        this.disconnect = this.disconnect.bind(this);
        this.reconnect = this.reconnect.bind(this);
        this.sendHeartbeat = this.sendHeartbeat.bind(this);
        this.handleConnect = this.handleConnect.bind(this);
        this.handleDisconnect = this.handleDisconnect.bind(this);
        this.handleReconnect = this.handleReconnect.bind(this);
        this.handleServerInfo = this.handleServerInfo.bind(this);
        this.handleAutoRejoin = this.handleAutoRejoin.bind(this);
        this.handleError = this.handleError.bind(this);

        // Initialize if options.autoConnect is true
        if (options.autoConnect) {
            this.connect();
        }
    }

    /**
     * Connect to the socket.io server
     */
    connect() {
        if (this.socket) {
            this.log('Socket already exists, disconnecting first');
            this.socket.disconnect();
        }

        this.log('Connecting to socket.io server');

        // Create new socket connection with reconnection disabled
        // We'll handle reconnection ourselves for more control
        this.socket = io({
            reconnection: false,
            timeout: 10000,
            forceNew: true
        });

        // Set up core event handlers
        this.socket.on('connect', this.handleConnect);
        this.socket.on('disconnect', this.handleDisconnect);
        this.socket.on('connect_error', this.handleError);
        this.socket.on('error', this.handleError);
        this.socket.on('server_info', this.handleServerInfo);
        this.socket.on('auto_rejoin', this.handleAutoRejoin);

        // Set up custom event handlers from eventHandlers object
        for (const [event, handler] of Object.entries(this.eventHandlers)) {
            // Remove any existing handler for this event to prevent duplicates
            this.socket.off(event);

            // Add the new handler with error handling and logging
            this.socket.on(event, (...args) => {
                // Log received events for debugging
                this.log(`Received event: ${event}`, args);

                try {
                    // Call the handler with the received arguments
                    handler(...args);
                } catch (error) {
                    this.log(`Error in handler for ${event}:`, error);
                    console.error(`Error in socket handler for ${event}:`, error);
                }
            });
        }
    }

    /**
     * Disconnect from the socket.io server
     */
    disconnect() {
        this.log('Disconnecting from socket.io server');

        if (this.socket) {
            this.socket.disconnect();
        }

        this.clearTimers();
        this.isConnected = false;
    }

    /**
     * Attempt to reconnect to the socket.io server
     */
    reconnect() {
        if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
            this.log('Max reconnect attempts reached, giving up');
            this.notifyReconnectionFailure();
            return;
        }

        this.reconnectAttempts++;
        this.log(`Reconnect attempt ${this.reconnectAttempts}/${this.options.maxReconnectAttempts}`);

        this.connect();
    }

    /**
     * Send a heartbeat to the server to keep the connection alive
     */
    sendHeartbeat() {
        if (!this.isConnected || !this.socket) {
            this.log('Cannot send heartbeat, not connected');
            return;
        }

        const heartbeatData = {
            client_timestamp: Date.now() / 1000,
            room_id: this.roomId,
            visitor_id: this.visitorId,
            admin_id: this.adminId
        };

        this.log('Sending heartbeat', heartbeatData);

        this.socket.emit('heartbeat', heartbeatData, (response) => {
            if (response && response.status === 'ok') {
                this.log('Heartbeat acknowledged', response);

                // Schedule the next heartbeat
                this.heartbeatTimer = setTimeout(
                    this.sendHeartbeat,
                    this.options.heartbeatInterval
                );
            } else {
                this.log('Heartbeat failed', response);
                // If heartbeat fails, try to reconnect
                this.handleDisconnect('heartbeat_failure');
            }
        });
    }

    /**
     * Handle successful connection
     */
    handleConnect() {
        this.log('Connected to socket.io server');
        this.isConnected = true;
        this.reconnectAttempts = 0;

        // Notify connection listeners
        this.connectionListeners.forEach(listener => listener(this.socket));

        // Start heartbeat
        this.sendHeartbeat();
    }

    /**
     * Handle disconnection
     */
    handleDisconnect(reason) {
        this.log(`Disconnected from socket.io server: ${reason}`);
        this.isConnected = false;

        // Clear any existing timers
        this.clearTimers();

        // Notify disconnection listeners
        this.disconnectionListeners.forEach(listener => listener(reason));

        // Attempt to reconnect if autoReconnect is enabled
        if (this.options.autoReconnect) {
            this.log(`Will attempt to reconnect in ${this.options.reconnectInterval}ms`);
            this.reconnectTimer = setTimeout(this.reconnect, this.options.reconnectInterval);
        }
    }

    /**
     * Handle reconnection
     */
    handleReconnect() {
        this.log('Reconnected to socket.io server');

        // Notify reconnection listeners
        this.reconnectionListeners.forEach(listener => listener(this.socket));

        // Rejoin rooms if needed
        if (this.roomId) {
            this.log(`Rejoining room: ${this.roomId}`);
            this.socket.emit('join_room', { room_id: this.roomId });
        }

        // Resend any pending messages
        if (this.pendingMessages.length > 0) {
            this.log(`Resending ${this.pendingMessages.length} pending messages`);

            this.pendingMessages.forEach(msg => {
                this.socket.emit(msg.event, msg.data);
            });

            this.pendingMessages = [];
        }
    }

    /**
     * Handle server info message
     */
    handleServerInfo(data) {
        this.log('Received server info', data);

        // Update heartbeat interval if provided
        if (data.ping_interval) {
            this.options.heartbeatInterval = data.ping_interval * 1000;
        }

        // Check if this is a reconnection
        if (data.is_reconnection) {
            this.handleReconnect();
        }
    }

    /**
     * Handle auto rejoin message
     */
    handleAutoRejoin(data) {
        this.log('Auto rejoined room', data);

        // Update room ID
        if (data.room_id) {
            this.roomId = data.room_id;
        }

        // Update visitor ID
        if (data.visitor_id) {
            this.visitorId = data.visitor_id;
        }

        // Update admin ID
        if (data.admin_id) {
            this.adminId = data.admin_id;
        }

        // Notify reconnection listeners if this is a reconnection
        if (data.is_reconnection) {
            this.reconnectionListeners.forEach(listener => listener(this.socket, data));
        }
    }

    /**
     * Handle connection or socket errors
     */
    handleError(error) {
        this.log('Socket error', error);

        // If we're not connected, try to reconnect
        if (!this.isConnected && this.options.autoReconnect) {
            this.log(`Will attempt to reconnect in ${this.options.reconnectInterval}ms`);
            this.reconnectTimer = setTimeout(this.reconnect, this.options.reconnectInterval);
        }
    }

    /**
     * Clear all timers
     */
    clearTimers() {
        if (this.heartbeatTimer) {
            clearTimeout(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }

        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
    }

    /**
     * Set room ID
     */
    setRoomId(roomId) {
        this.roomId = roomId;

        // Join room if connected
        if (this.isConnected && this.socket) {
            this.socket.emit('join_room', { room_id: roomId });
        }
    }

    /**
     * Set visitor ID
     */
    setVisitorId(visitorId) {
        this.visitorId = visitorId;
    }

    /**
     * Set admin ID
     */
    setAdminId(adminId) {
        this.adminId = adminId;
    }

    /**
     * Add event handler
     */
    on(event, handler) {
        // Store the handler in our eventHandlers object
        this.eventHandlers[event] = handler;

        // If socket already exists, add the handler
        if (this.socket) {
            // Remove any existing handler for this event to prevent duplicates
            this.socket.off(event);

            // Add the new handler
            this.socket.on(event, (...args) => {
                // Log received events for debugging
                this.log(`Received event: ${event}`, args);

                try {
                    // Call the handler with the received arguments
                    handler(...args);
                } catch (error) {
                    this.log(`Error in handler for ${event}:`, error);
                }
            });
        }
    }

    /**
     * Remove event handler
     */
    off(event) {
        delete this.eventHandlers[event];

        // If socket exists, remove the handler
        if (this.socket) {
            this.socket.off(event);
        }
    }

    /**
     * Add connection listener
     */
    onConnect(listener) {
        this.connectionListeners.push(listener);
    }

    /**
     * Add disconnection listener
     */
    onDisconnect(listener) {
        this.disconnectionListeners.push(listener);
    }

    /**
     * Add reconnection listener
     */
    onReconnect(listener) {
        this.reconnectionListeners.push(listener);
    }

    /**
     * Emit event to server
     */
    emit(event, data, callback) {
        if (!this.isConnected || !this.socket) {
            this.log(`Cannot emit ${event}, not connected. Adding to pending queue.`);

            // Store message to send when reconnected
            this.pendingMessages.push({ event, data });

            return false;
        }

        // Log all emitted events for debugging
        this.log(`Emitting event: ${event}`, data);

        try {
            this.socket.emit(event, data, callback);
            return true;
        } catch (error) {
            this.log(`Error emitting ${event}:`, error);
            return false;
        }
    }

    /**
     * Notify about reconnection failure
     */
    notifyReconnectionFailure() {
        // Create and dispatch a custom event
        const event = new CustomEvent('socket_reconnect_failed', {
            detail: {
                attempts: this.reconnectAttempts,
                maxAttempts: this.options.maxReconnectAttempts
            }
        });

        document.dispatchEvent(event);
    }

    /**
     * Log message if debug is enabled
     */
    log(...args) {
        if (this.options.debug) {
            console.log('[SocketManager]', ...args);
        }
    }
}

// Export for use in other modules
window.SocketManager = SocketManager;
