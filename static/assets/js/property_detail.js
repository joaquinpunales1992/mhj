document.addEventListener('DOMContentLoaded', function() {
    // Variables for date and time selection
    let selectedDate = null;
    let selectedTime = null;
    
    // Calendar functionality
    const calendarContainer = document.getElementById('calendar-container');
    const currentMonthElement = document.getElementById('current-month');
    const prevMonthButton = document.getElementById('prev-month');
    const nextMonthButton = document.getElementById('next-month');
    
    // Time slots container
    const timeSlotsContainer = document.getElementById('time-slots');
    
    // Book button
    const bookButton = document.getElementById('book-button');
    
    // Success confirmation
    const successConfirmation = document.getElementById('success-confirmation');
    const bookedDateElement = document.getElementById('booked-date');
    const bookedTimeElement = document.getElementById('booked-time');
    
    // Current date for calendar
    let currentDate = new Date();
    
    // Debug: Check if elements are found
    console.log('Calendar container:', calendarContainer);
    console.log('Current month element:', currentMonthElement);
    console.log('Prev month button:', prevMonthButton);
    console.log('Next month button:', nextMonthButton);
    
    // Generate and render the calendar
    function renderCalendar(year, month) {
      // Clear the calendar container
      calendarContainer.innerHTML = '';
      
      // Update the month display
      const monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 
                           'July', 'August', 'September', 'October', 'November', 'December'];
      currentMonthElement.textContent = `${monthNames[month]} ${year}`;
      
      // Get the first day of the month
      const firstDay = new Date(year, month, 1);
      
      // Get the last day of the month
      const lastDay = new Date(year, month + 1, 0);
      
      // Get the day of the week for the first day (0 = Sunday, 6 = Saturday)
      const firstDayIndex = firstDay.getDay();
      
      // Add day names
      const dayNames = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
      dayNames.forEach(day => {
        const dayElement = document.createElement('div');
        dayElement.classList.add('text-center', 'text-xs', 'font-medium', 'text-secondary', 'py-2');
        dayElement.textContent = day;
        calendarContainer.appendChild(dayElement);
      });
      
      // Add empty cells for days before the first day of the month
      for (let i = 0; i < firstDayIndex; i++) {
        const emptyCell = document.createElement('div');
        emptyCell.classList.add('calendar-empty-cell');
        calendarContainer.appendChild(emptyCell);
      }
      
      // Get today's date for comparison
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      
      // Add days of the month
      for (let day = 1; day <= lastDay.getDate(); day++) {
        const dayElement = document.createElement('div');
        const date = new Date(year, month, day);
        date.setHours(0, 0, 0, 0); // Normalize time for comparison
        
        // Base styling
        dayElement.classList.add('h-10', 'flex', 'items-center', 'justify-center', 'text-sm', 'calendar-day');
        
        // Check if this date is before today (disable past dates)
        const isPastDate = date < today;
        
        if (isPastDate) {
          dayElement.classList.add('calendar-day-disabled');
        } else {
          // Check if this is the selected date
          if (selectedDate && 
              date.getDate() === selectedDate.getDate() && 
              date.getMonth() === selectedDate.getMonth() && 
              date.getFullYear() === selectedDate.getFullYear()) {
            dayElement.classList.add('calendar-day-selected');
          }
          
          // Add click event to select the date
          dayElement.addEventListener('click', function() {
            // Remove selected class from previously selected date
            const previouslySelected = document.querySelector('.calendar-day-selected');
            if (previouslySelected) {
              previouslySelected.classList.remove('calendar-day-selected');
            }
            
            // Add selected class to the clicked date
            dayElement.classList.add('calendar-day-selected');
            
            // Update the selected date
            selectedDate = new Date(year, month, day);
            
            // Update the book button state
            updateBookButtonState();
            
            console.log('Selected date:', selectedDate);
          });
        }
        
        dayElement.textContent = day;
        calendarContainer.appendChild(dayElement);
      }
    }
    
    // Generate time slots
    function generateTimeSlots() {
      // Clear the container
      timeSlotsContainer.innerHTML = '';
      
      // Define time slots from 9 AM to 6 PM
      const unavailableSlots = ['9:00 AM', '10:00 AM', '6:00 PM']; // Example of unavailable slots
      
      for (let hour = 9; hour <= 18; hour++) {
        const hourFormatted = hour <= 12 ? hour : hour - 12;
        const amPm = hour < 12 ? 'AM' : 'PM';
        const time = `${hourFormatted}:00 ${amPm}`;
        
        const isDisabled = unavailableSlots.includes(time);
        
        const timeElement = document.createElement('button');
        timeElement.textContent = time;
        timeElement.classList.add('h-10', 'rounded-md', 'flex', 'items-center', 'justify-center', 'text-sm', 'border', 'border-accent/10');
        
        if (isDisabled) {
          timeElement.classList.add('time-slot-disabled');
          timeElement.disabled = true;
        } else {
          timeElement.classList.add('time-slot');
          
          // Check if this is the selected time
          if (time === selectedTime) {
            timeElement.classList.add('time-slot-selected');
          }
          
          // Add click event to select the time
          timeElement.addEventListener('click', function() {
            // Remove selected class from previously selected time
            const previouslySelected = document.querySelector('.time-slot-selected');
            if (previouslySelected) {
              previouslySelected.classList.remove('time-slot-selected');
            }
            
            // Add selected class to the clicked time
            timeElement.classList.add('time-slot-selected');
            
            // Update the selected time
            selectedTime = time;
            
            // Update the book button state
            updateBookButtonState();
            
            console.log('Selected time:', selectedTime);
          });
        }
        
        timeSlotsContainer.appendChild(timeElement);
      }
      
      // Auto-select first available time slot if none selected
      if (!selectedTime) {
        const firstAvailable = timeSlotsContainer.querySelector('.time-slot:not(.time-slot-disabled)');
        if (firstAvailable) {
          firstAvailable.click();
        }
      }
    }
    
    // Update the book button state based on selections
    function updateBookButtonState() {
      if (selectedDate && selectedTime) {
        bookButton.disabled = false;
      } else {
        bookButton.disabled = true;
      }
    }
    
    // Format date for display
    function formatDate(date) {
      if (!date) return '';
      const monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 
                         'July', 'August', 'September', 'October', 'November', 'December'];
      return `${monthNames[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
    }
    
    // Get end time (1 hour after start time)
    function getEndTime(startTime) {
      if (!startTime) return '';
      const [hourStr, period] = startTime.split(':');
      const hour = parseInt(hourStr);
      const nextHour = hour === 12 ? 1 : hour + 1;
      return `${nextHour}:00 ${period.split(' ')[1]}`;
    }
    
    // Month navigation events
    prevMonthButton.addEventListener('click', function() {
      currentDate.setMonth(currentDate.getMonth() - 1);
      renderCalendar(currentDate.getFullYear(), currentDate.getMonth());
    });
    
    nextMonthButton.addEventListener('click', function() {
      currentDate.setMonth(currentDate.getMonth() + 1);
      renderCalendar(currentDate.getFullYear(), currentDate.getMonth());
    });
    
    // Book button click event
    bookButton.addEventListener('click', function(event) {
      // Show loading state
      const originalText = bookButton.textContent;
      bookButton.textContent = 'Booking...';
      bookButton.disabled = true;
      
      // Create a ripple effect
      const ripple = document.createElement('span');
      ripple.classList.add('ripple');
      const rect = bookButton.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height);
      ripple.style.width = ripple.style.height = `${size}px`;
      ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
      ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
      bookButton.appendChild(ripple);
      
      // Send booking confirmation email
        fetch('/api/send-booking-confirmation', {
            method: 'POST',
            headers: {
            'Content-Type': 'application/json'
            },
            body: JSON.stringify({
            date: selectedDate,
            time: selectedTime,
            })
        })
        .then(response => {
            if (!response.ok) {
            throw new Error('Failed to send confirmation email.');
            }
            return response.json();
        })
        .then(data => {
            // Remove ripple
            ripple.remove();
        
            // Show success confirmation
            bookedDateElement.textContent = formatDate(selectedDate);
            bookedTimeElement.textContent = `${selectedTime} - ${getEndTime(selectedTime)}`;
            successConfirmation.classList.remove('hidden');
        
            // Scroll to the success message
            successConfirmation.scrollIntoView({ behavior: 'smooth' });
        
            // Reset button state
            bookButton.textContent = originalText;
        
            console.log('Booking confirmed for:', {
            date: selectedDate,
            time: selectedTime
            });
        })
        .catch(error => {
            ripple.remove();
            bookButton.textContent = originalText;
            bookButton.disabled = false;
            alert('There was an error sending the confirmation email. Please try again.');
            console.error(error);
        });
    });
    
    // Initial render
    renderCalendar(currentDate.getFullYear(), currentDate.getMonth());
    generateTimeSlots();
    updateBookButtonState();
  });