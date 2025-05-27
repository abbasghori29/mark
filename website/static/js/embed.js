/**
 * Chat Widget Embed Script
 * This script can be embedded on any website to load the chat widget.
 */

(function() {
    // Configuration (will be replaced with actual values when served)
    const config = {
        serverUrl: '{{server_url}}',
        primaryColor: '{{primary_color}}',
        position: '{{position}}', // 'right' or 'left'
        welcomeMessage: '{{welcome_message}}',
        companyName: '{{company_name}}',
        logoUrl: '{{logo_url}}'
    };

    // Create widget container
    function createWidgetContainer() {
        const container = document.createElement('div');
        container.id = 'chat-widget-container';
        container.style.position = 'fixed';
        container.style.bottom = '20px';
        container.style[config.position] = '20px';
        container.style.zIndex = '9999';
        container.style.display = 'flex';
        container.style.flexDirection = 'column';
        container.style.maxHeight = '600px';
        container.style.boxShadow = '0 5px 40px rgba(0, 0, 0, 0.16)';
        container.style.borderRadius = '16px';
        container.style.overflow = 'hidden';
        container.style.transition = 'all 0.3s ease';
        container.style.opacity = '0';
        container.style.transform = 'translateY(20px)';
        
        // Start with just the button visible
        container.style.width = '60px';
        container.style.height = '60px';
        
        return container;
    }

    // Create chat button
    function createChatButton() {
        const button = document.createElement('div');
        button.id = 'chat-widget-button';
        button.style.width = '60px';
        button.style.height = '60px';
        button.style.borderRadius = '50%';
        button.style.backgroundColor = config.primaryColor;
        button.style.display = 'flex';
        button.style.justifyContent = 'center';
        button.style.alignItems = 'center';
        button.style.cursor = 'pointer';
        button.style.position = 'absolute';
        button.style.bottom = '0';
        button.style[config.position] = '0';
        button.style.boxShadow = '0 4px 8px rgba(0, 0, 0, 0.2)';
        button.style.zIndex = '10000';
        
        // Chat icon
        button.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M20 2H4C2.9 2 2 2.9 2 4V22L6 18H20C21.1 18 22 17.1 22 16V4C22 2.9 21.1 2 20 2Z" fill="white"/>
            </svg>
        `;
        
        return button;
    }

    // Create chat window
    function createChatWindow() {
        const chatWindow = document.createElement('div');
        chatWindow.id = 'chat-widget-window';
        chatWindow.style.display = 'none';
        chatWindow.style.flexDirection = 'column';
        chatWindow.style.width = '350px';
        chatWindow.style.height = '500px';
        chatWindow.style.backgroundColor = '#fff';
        chatWindow.style.borderRadius = '16px';
        chatWindow.style.overflow = 'hidden';
        chatWindow.style.transition = 'all 0.3s ease';
        
        // Chat header
        const header = document.createElement('div');
        header.style.padding = '15px';
        header.style.backgroundColor = config.primaryColor;
        header.style.color = '#fff';
        header.style.fontFamily = 'Arial, sans-serif';
        header.style.display = 'flex';
        header.style.justifyContent = 'space-between';
        header.style.alignItems = 'center';
        
        // Company name/logo
        const companyInfo = document.createElement('div');
        companyInfo.style.display = 'flex';
        companyInfo.style.alignItems = 'center';
        
        if (config.logoUrl) {
            const logo = document.createElement('img');
            logo.src = config.logoUrl;
            logo.style.height = '24px';
            logo.style.marginRight = '8px';
            companyInfo.appendChild(logo);
        }
        
        const companyName = document.createElement('div');
        companyName.textContent = config.companyName;
        companyName.style.fontWeight = 'bold';
        companyInfo.appendChild(companyName);
        
        header.appendChild(companyInfo);
        
        // Minimize button
        const minimizeBtn = document.createElement('div');
        minimizeBtn.id = 'chat-widget-minimize';
        minimizeBtn.style.cursor = 'pointer';
        minimizeBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M19 13H5V11H19V13Z" fill="white"/>
            </svg>
        `;
        header.appendChild(minimizeBtn);
        
        // Chat iframe
        const iframe = document.createElement('iframe');
        iframe.id = 'chat-iframe';
        iframe.style.width = '100%';
        iframe.style.height = '100%';
        iframe.style.border = 'none';
        iframe.style.flexGrow = '1';
        
        chatWindow.appendChild(header);
        chatWindow.appendChild(iframe);
        
        return chatWindow;
    }

    // Initialize the widget
    function initWidget() {
        // Create elements
        const container = createWidgetContainer();
        const chatButton = createChatButton();
        const chatWindow = createChatWindow();
        
        // Add elements to DOM
        container.appendChild(chatButton);
        container.appendChild(chatWindow);
        document.body.appendChild(container);
        
        // Show container with animation
        setTimeout(() => {
            container.style.opacity = '1';
            container.style.transform = 'translateY(0)';
        }, 100);
        
        // Track state
        let isOpen = false;
        let isMinimized = false;
        
        // Toggle chat window
        chatButton.addEventListener('click', () => {
            if (!isOpen || isMinimized) {
                openChat();
            } else {
                closeChat();
            }
        });
        
        // Minimize chat window
        document.getElementById('chat-widget-minimize').addEventListener('click', (e) => {
            e.stopPropagation();
            minimizeChat();
        });
        
        // Open chat function
        function openChat() {
            const iframe = document.getElementById('chat-iframe');
            
            // Load iframe if not already loaded
            if (iframe.src === '' || iframe.src === 'about:blank') {
                // Generate a visitor ID if not already stored
                let visitorId = localStorage.getItem('chat_visitor_id');
                if (!visitorId) {
                    visitorId = generateUUID();
                    localStorage.setItem('chat_visitor_id', visitorId);
                }
                
                // Get browser UUID
                let browserUUID = localStorage.getItem('browserUUID');
                if (!browserUUID) {
                    browserUUID = generateUUID();
                    localStorage.setItem('browserUUID', browserUUID);
                }
                
                // Check if we need to create a new chat room
                fetch(`${config.serverUrl}/api/check_visitor`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Browser-UUID': browserUUID
                    },
                    body: JSON.stringify({ 
                        visitor_id: visitorId,
                        browser_uuid: browserUUID
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success && data.room_id) {
                        iframe.src = `${config.serverUrl}/chat_iframe/${data.room_id}`;
                    }
                })
                .catch(error => {
                    console.error('Error checking visitor:', error);
                });
            }
            
            // Show chat window
            container.style.width = '350px';
            container.style.height = '500px';
            chatWindow.style.display = 'flex';
            isOpen = true;
            isMinimized = false;
        }
        
        // Close chat function
        function closeChat() {
            container.style.width = '60px';
            container.style.height = '60px';
            chatWindow.style.display = 'none';
            isOpen = false;
        }
        
        // Minimize chat function
        function minimizeChat() {
            container.style.width = '60px';
            container.style.height = '60px';
            chatWindow.style.display = 'none';
            isMinimized = true;
        }
        
        // Generate UUID for visitor tracking
        function generateUUID() {
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
        }
    }
    
    // Initialize when DOM is loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initWidget);
    } else {
        initWidget();
    }
})();
