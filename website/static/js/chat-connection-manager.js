/**
 * Chat Connection Manager - Manages multiple concurrent chat connections
 * 
 * This module handles multiple chat sessions, ensuring proper isolation
 * and efficient resource management for concurrent connections.
 */

class ChatConnectionManager {
    constructor() {
        this.connections = new Map();  // Map of connectionId -> SocketManager
        this.roomConnections = new Map();  // Map of roomId -> connectionId
        this.maxConnections = 10;  // Maximum concurrent connections
        this.connectionTimeout = 30000;  // 30 seconds timeout for inactive connections
        this.cleanupInterval = 60000;  // 1 minute cleanup interval
        
        // Start periodic cleanup
        this.startCleanup();
        
        // Bind methods
        this.createConnection = this.createConnection.bind(this);
        this.getConnection = this.getConnection.bind(this);
        this.removeConnection = this.removeConnection.bind(this);
        this.cleanup = this.cleanup.bind(this);
    }

    /**
     * Create a new chat connection
     */
    createConnection(roomId, visitorId, adminId = null, options = {}) {
        // Check if we already have a connection for this room
        if (this.roomConnections.has(roomId)) {
            const existingConnectionId = this.roomConnections.get(roomId);
            const existingConnection = this.connections.get(existingConnectionId);
            
            if (existingConnection && existingConnection.isConnected) {
                console.log(`Reusing existing connection for room ${roomId}`);
                return existingConnection;
            } else {
                // Remove stale connection
                this.removeConnection(existingConnectionId);
            }
        }

        // Check connection limit
        if (this.connections.size >= this.maxConnections) {
            console.warn(`Maximum connections (${this.maxConnections}) reached. Cleaning up oldest connections.`);
            this.cleanupOldestConnections(1);
        }

        // Create new socket manager with optimized settings
        const socketManager = new SocketManager({
            debug: options.debug || false,
            autoReconnect: true,
            maxReconnectAttempts: 5,
            reconnectInterval: 2000,
            heartbeatInterval: 20000,
            connectionTimeout: 8000,
            ...options
        });

        // Set session information
        socketManager.setSession(roomId, visitorId, adminId);

        // Store the connection
        const connectionId = socketManager.connectionId;
        this.connections.set(connectionId, socketManager);
        this.roomConnections.set(roomId, connectionId);

        // Set up connection event handlers
        socketManager.onConnect(() => {
            console.log(`Chat connection established for room ${roomId}`);
        });

        socketManager.onDisconnect((reason) => {
            console.log(`Chat connection lost for room ${roomId}: ${reason}`);
        });

        socketManager.onReconnect(() => {
            console.log(`Chat connection restored for room ${roomId}`);
        });

        console.log(`Created new chat connection for room ${roomId} (${connectionId})`);
        return socketManager;
    }

    /**
     * Get an existing connection by room ID
     */
    getConnection(roomId) {
        const connectionId = this.roomConnections.get(roomId);
        if (connectionId) {
            return this.connections.get(connectionId);
        }
        return null;
    }

    /**
     * Get connection by connection ID
     */
    getConnectionById(connectionId) {
        return this.connections.get(connectionId);
    }

    /**
     * Remove a connection
     */
    removeConnection(connectionId) {
        const connection = this.connections.get(connectionId);
        if (connection) {
            // Find and remove room mapping
            for (const [roomId, connId] of this.roomConnections.entries()) {
                if (connId === connectionId) {
                    this.roomConnections.delete(roomId);
                    break;
                }
            }

            // Disconnect and remove
            connection.disconnect();
            this.connections.delete(connectionId);
            console.log(`Removed connection ${connectionId}`);
        }
    }

    /**
     * Remove connection by room ID
     */
    removeConnectionByRoom(roomId) {
        const connectionId = this.roomConnections.get(roomId);
        if (connectionId) {
            this.removeConnection(connectionId);
        }
    }

    /**
     * Clean up old or inactive connections
     */
    cleanupOldestConnections(count = 1) {
        const sortedConnections = Array.from(this.connections.entries())
            .sort((a, b) => {
                const aStats = a[1].getConnectionStats();
                const bStats = b[1].getConnectionStats();
                return (aStats.connectionStartTime || 0) - (bStats.connectionStartTime || 0);
            });

        for (let i = 0; i < Math.min(count, sortedConnections.length); i++) {
            const [connectionId] = sortedConnections[i];
            console.log(`Cleaning up old connection: ${connectionId}`);
            this.removeConnection(connectionId);
        }
    }

    /**
     * Periodic cleanup of inactive connections
     */
    cleanup() {
        const now = Date.now();
        const connectionsToRemove = [];

        for (const [connectionId, connection] of this.connections.entries()) {
            const stats = connection.getConnectionStats();
            
            // Remove connections that are not connected and haven't been active recently
            if (!stats.isConnected && 
                stats.connectionStartTime && 
                (now - stats.connectionStartTime) > this.connectionTimeout) {
                connectionsToRemove.push(connectionId);
            }
        }

        connectionsToRemove.forEach(connectionId => {
            console.log(`Cleaning up inactive connection: ${connectionId}`);
            this.removeConnection(connectionId);
        });

        if (connectionsToRemove.length > 0) {
            console.log(`Cleaned up ${connectionsToRemove.length} inactive connections`);
        }
    }

    /**
     * Start periodic cleanup
     */
    startCleanup() {
        setInterval(this.cleanup, this.cleanupInterval);
    }

    /**
     * Get statistics about all connections
     */
    getStats() {
        const stats = {
            totalConnections: this.connections.size,
            connectedCount: 0,
            disconnectedCount: 0,
            reconnectingCount: 0,
            connections: []
        };

        for (const [connectionId, connection] of this.connections.entries()) {
            const connStats = connection.getConnectionStats();
            stats.connections.push(connStats);

            if (connStats.isConnected) {
                stats.connectedCount++;
            } else if (connStats.reconnectAttempts > 0) {
                stats.reconnectingCount++;
            } else {
                stats.disconnectedCount++;
            }
        }

        return stats;
    }

    /**
     * Disconnect all connections
     */
    disconnectAll() {
        console.log(`Disconnecting all ${this.connections.size} connections`);
        
        for (const [connectionId, connection] of this.connections.entries()) {
            connection.disconnect();
        }

        this.connections.clear();
        this.roomConnections.clear();
    }

    /**
     * Set maximum number of concurrent connections
     */
    setMaxConnections(max) {
        this.maxConnections = max;
        
        // Clean up excess connections if needed
        if (this.connections.size > max) {
            const excess = this.connections.size - max;
            this.cleanupOldestConnections(excess);
        }
    }
}

// Create global instance
window.chatConnectionManager = new ChatConnectionManager();

// Export for use in other modules
window.ChatConnectionManager = ChatConnectionManager;
