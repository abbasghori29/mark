document.addEventListener('DOMContentLoaded', function() {
    // Connect to WebSocket
    const socket = io();

    // Join the admin notification channel
    socket.emit('join_admin_channel');

    // Handle new message notifications
    socket.on('new_message_notification', function(data) {
        // Only process notifications for visitor messages (not admin messages)
        if (data.is_from_visitor) {
            // Play notification sound ONLY if page is not visible (admin is not actively viewing)
            if (document.visibilityState !== 'visible') {
                const notificationSound = document.getElementById('notification-sound');
                if (notificationSound) {
                    notificationSound.play().catch(error => {
                        console.log('Could not play notification sound:', error);
                    });
                }
            }

            // Flash the title to alert the admin (only if page is not visible)
            if (document.visibilityState !== 'visible') {
                let originalTitle = document.title;
                let newMessageTitle = "New Message! - Admin Dashboard";
                let titleInterval = setInterval(function() {
                    document.title = document.title === originalTitle ? newMessageTitle : originalTitle;
                }, 1000);

                // Clear the interval when the user focuses on the window
                window.addEventListener('focus', function() {
                    clearInterval(titleInterval);
                    document.title = originalTitle;
                }, { once: true });
            }

            // Show browser notification if enabled (only if page is not visible)
            if (document.visibilityState !== 'visible' && 'Notification' in window && Notification.permission === 'granted') {
                const notification = new Notification('New Chat Message', {
                    body: 'You have a new message from a visitor',
                    icon: '/static/img/logo.png'
                });

                notification.onclick = function() {
                    window.focus();
                    this.close();
                };
            }

            // If on dashboard page, refresh after a short delay
            if (window.location.pathname.endsWith('/dashboard')) {
                setTimeout(function() {
                    location.reload();
                }, 2000);
            }
        }
    });

    // Handle visitor updates
    socket.on('visitor_update', function(data) {
        // If on visitor tracking page, refresh after a short delay
        if (window.location.pathname.endsWith('/visitors')) {
            setTimeout(function() {
                location.reload();
            }, 2000);
        }
    });

    // Close chat functionality
    const closeButtons = document.querySelectorAll('.close-chat');
    closeButtons.forEach(function(button) {
        button.addEventListener('click', function() {
            const roomId = this.getAttribute('data-room-id');
            if (confirm('Are you sure you want to close this chat?')) {
                fetch(`/admin/api/close_chat/${roomId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        location.reload();
                    } else {
                        alert('Failed to close chat: ' + data.message);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('An error occurred while closing the chat.');
                });
            }
        });
    });

    // Toggle online status
    const statusToggle = document.querySelector('.toggle-status');
    if (statusToggle) {
        statusToggle.addEventListener('click', function() {
            fetch('/admin/api/toggle_online_status', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({})
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                } else {
                    // Only show alert if there's an actual error and data.error exists
                    if (data.error) {
                        alert('Failed to toggle status: ' + data.error);
                    }
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('An error occurred while toggling status.');
            });
        });
    }

    // Request notification permission
    if ('Notification' in window && Notification.permission !== 'granted' && Notification.permission !== 'denied') {
        document.getElementById('notification-permission').style.display = 'block';

        document.getElementById('enable-notifications').addEventListener('click', function() {
            Notification.requestPermission().then(function(permission) {
                if (permission === 'granted') {
                    document.getElementById('notification-permission').style.display = 'none';
                }
            });
        });

        document.getElementById('dismiss-notification').addEventListener('click', function() {
            document.getElementById('notification-permission').style.display = 'none';
        });
    }
});