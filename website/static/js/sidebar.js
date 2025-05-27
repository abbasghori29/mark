document.addEventListener('DOMContentLoaded', function() {
    const appContainer = document.getElementById('app-container');
    const menuToggle = document.getElementById('menu-toggle');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const mainWrapper = document.querySelector('.main-wrapper');
    
    // Check if sidebar state is stored in localStorage
    const sidebarOpen = localStorage.getItem('sidebarOpen') === 'true';
    if (sidebarOpen) {
        appContainer.classList.add('sidebar-open');
        // Add margin to main content when sidebar is open on page load
        if (mainWrapper) {
            mainWrapper.style.marginLeft = '260px';
        }
    }
    
    // Toggle sidebar
    function toggleSidebar() {
        appContainer.classList.toggle('sidebar-open');
        const isOpen = appContainer.classList.contains('sidebar-open');
        
        // Adjust main content margin based on sidebar state
        if (mainWrapper) {
            mainWrapper.style.marginLeft = isOpen ? '260px' : '0';
            // Add transition for smooth movement
            mainWrapper.style.transition = 'margin-left 0.3s ease';
        }
        
        localStorage.setItem('sidebarOpen', isOpen);
    }
    
    // Event listeners
    if (menuToggle) {
        menuToggle.addEventListener('click', toggleSidebar);
    }
    
    if (sidebarOverlay) {
        sidebarOverlay.addEventListener('click', toggleSidebar);
    }
    
    // Close sidebar with Escape key
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && appContainer.classList.contains('sidebar-open')) {
            toggleSidebar();
        }
    });
    
    // Handle responsive behavior
    function handleResponsive() {
        if (window.innerWidth <= 768 && mainWrapper) {
            // On mobile, don't push content
            mainWrapper.style.marginLeft = '0';
        } else if (appContainer.classList.contains('sidebar-open') && mainWrapper) {
            // On desktop, push content if sidebar is open
            mainWrapper.style.marginLeft = '260px';
        }
    }
    
    // Initial check and event listener for resize
    handleResponsive();
    window.addEventListener('resize', handleResponsive);
}); 