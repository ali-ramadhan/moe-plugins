// ==UserScript==
// @name         RYM Genre Copier
// @namespace    http://tampermonkey.net/
// @version      1.3
// @description  Adds a button to RateYourMusic album pages to copy primary and secondary genres to the clipboard.
// @author       Me
// @match        https://rateyourmusic.com/release/album/*
// @match        https://rateyourmusic.com/release/ep/*
// @match        https://rateyourmusic.com/release/single/*
// @grant        GM_setClipboard
// @grant        GM_addStyle
// ==/UserScript==

(function() {
    'use strict';

    GM_addStyle(`
        .copy-genres-btn {
            background-color: #2c6eb5;
            color: white;
            padding: 8px 12px;
            border: none;
            border-radius: 5px;
            font-weight: bold;
            font-size: 12px;
            cursor: pointer;
            margin-left: 15px;
            transition: background-color 0.2s;
            display: inline-block;
            vertical-align: middle;
        }
        .copy-genres-btn:hover {
            background-color: #3b82f6;
        }
        .copy-genres-btn.copied {
            background-color: #16a34a; /* Green color for success */
        }
    `);

    function copyGenres(event) {
        // Prevent any default link behavior
        event.preventDefault();
        event.stopPropagation();

        const primaryGenreElements = Array.from(document.querySelectorAll('.release_pri_genres .genre'));
        const secondaryGenreElements = Array.from(document.querySelectorAll('.release_sec_genres .genre'));

        const primaryGenreTexts = primaryGenreElements.map(el => el.textContent.trim());
        const secondaryGenreTexts = secondaryGenreElements.map(el => el.textContent.trim());

        const allGenreTexts = [...primaryGenreTexts, ...secondaryGenreTexts];

        if (allGenreTexts.length === 0) {
            console.log("No genres found on this page.");
            return;
        }

        const genreString = allGenreTexts.join(';');

        // Copy the string to the clipboard using the script host's API
        GM_setClipboard(genreString);

        const button = document.querySelector('.copy-genres-btn');
        const originalText = button.textContent;
        button.textContent = 'Copied!';
        button.classList.add('copied');

        setTimeout(() => {
            button.textContent = originalText;
            button.classList.remove('copied');
        }, 2000);
    }

    // Wait for the page to be fully loaded before trying to add the button
    window.addEventListener('load', () => {
        const genresHeader = document.querySelector('.release_genres .info_hdr');

        if (genresHeader) {
            const copyButton = document.createElement('button');
            copyButton.textContent = 'Copy';
            copyButton.className = 'copy-genres-btn';

            copyButton.addEventListener('click', copyGenres);

            genresHeader.appendChild(copyButton);
        } else {
            console.log("Could not find the genre section header to attach the button.");
        }
    });

})();
