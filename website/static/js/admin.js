document.addEventListener('DOMContentLoaded', function() {
    // Mobile menu functionality
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const adminSidebar = document.getElementById('admin-sidebar');
    
    if (mobileMenuBtn && adminSidebar) {
        mobileMenuBtn.addEventListener('click', function() {
            adminSidebar.classList.toggle('show');
        });
        
        // Close sidebar when clicking outside on mobile
        document.addEventListener('click', function(event) {
            if (window.innerWidth <= 768 && 
                !adminSidebar.contains(event.target) && 
                !mobileMenuBtn.contains(event.target) && 
                adminSidebar.classList.contains('show')) {
                adminSidebar.classList.remove('show');
            }
        });
    }
    
    // Tab navigation
    const tabButtons = document.querySelectorAll('.nav-item');
    const tabPanes = document.querySelectorAll('.tab-pane');
    
    tabButtons.forEach(button => {
        button.addEventListener('click', function() {
            const tabId = this.getAttribute('data-tab');
            
            // Update active states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            this.classList.add('active');
            
            // Show selected tab
            tabPanes.forEach(pane => {
                if (pane.id === tabId) {
                    pane.classList.remove('hidden');
                } else {
                    pane.classList.add('hidden');
                }
            });
            
            // Close mobile menu after tab selection
            if (window.innerWidth <= 768 && adminSidebar) {
                adminSidebar.classList.remove('show');
            }
        });
    });
    
    // Profile dropdown
    const profileButton = document.querySelector('.btn-primary');
    const dropdownMenu = document.querySelector('.dropdown-menu');
    
    if (profileButton && dropdownMenu) {
        profileButton.addEventListener('click', function(event) {
            event.stopPropagation();
            dropdownMenu.classList.toggle('hidden');
        });
        
        document.addEventListener('click', function(event) {
            if (!dropdownMenu.contains(event.target)) {
                dropdownMenu.classList.add('hidden');
            }
        });
    }
    
    // Online status toggle
    const onlineStatus = document.getElementById('online-status');
    
    if (onlineStatus) {
        const statusText = onlineStatus.nextElementSibling.nextElementSibling;
        
        onlineStatus.addEventListener('change', function() {
            const isOnline = this.checked;
            statusText.textContent = isOnline ? 'Online' : 'Offline';
            
            // Send status update to server
            fetch('/admin/update-status', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ is_online: isOnline })
            });
        });
    }
    
    // Chat mode toggle
    const chatModeToggles = document.querySelectorAll('.chat-mode-toggle select');
    
    chatModeToggles.forEach(toggle => {
        toggle.addEventListener('change', function() {
            const roomId = this.closest('.chat-mode-toggle').getAttribute('data-room-id');
            const mode = this.value;
            
            fetch('/admin/update-chat-mode', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    room_id: roomId,
                    chat_mode: mode
                })
            });
        });
    });
    
    // Handle responsive table
    function handleResponsiveTable() {
        const tableContainer = document.querySelector('.table-container');
        if (tableContainer) {
            if (window.innerWidth <= 768) {
                const headers = Array.from(tableContainer.querySelectorAll('th')).map(th => th.textContent.trim());
                const rows = tableContainer.querySelectorAll('tbody tr');
                
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    cells.forEach((cell, index) => {
                        if (!cell.getAttribute('data-label')) {
                            cell.setAttribute('data-label', headers[index]);
                        }
                    });
                });
            }
        }
    }
    
    // Initial call and event listener for window resize
    handleResponsiveTable();
    window.addEventListener('resize', handleResponsiveTable);
    
    // Handle chat item selection
    const chatItems = document.querySelectorAll('.chat-item');
    
    chatItems.forEach(item => {
        item.addEventListener('click', function() {
            const roomId = this.getAttribute('data-room-id');
            
            // Remove active state from all items
            chatItems.forEach(i => i.classList.remove('bg-blue-100'));
            
            // Add active state to clicked item
            this.classList.add('bg-blue-100');
            
            // Load chat details
            fetch(`/admin/chat-details/${roomId}`)
                .then(response => response.json())
                .then(data => {
                    const detailsContainer = document.getElementById('chat-details-container');
                    if (detailsContainer) {
                        // Update the details container with the fetched data
                        detailsContainer.innerHTML = `
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div class="bg-white p-4 rounded-lg shadow-sm">
                                    <h4 class="font-semibold text-gray-700 mb-2">Visitor Information</h4>
                                    <p class="text-sm text-gray-600">IP: ${data.visitor_ip}</p>
                                    <p class="text-sm text-gray-600">Session Started: ${data.start_time}</p>
                                </div>
                                <div class="bg-white p-4 rounded-lg shadow-sm">
                                    <h4 class="font-semibold text-gray-700 mb-2">Chat Statistics</h4>
                                    <p class="text-sm text-gray-600">Total Messages: ${data.message_count}</p>
                                    <p class="text-sm text-gray-600">Status: ${data.status}</p>
                                </div>
                            </div>
                        `;
                    }
                });
        });
    });
}); 